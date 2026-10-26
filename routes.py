import csv
import io
import math
import re
from datetime import datetime
from flask import Response, render_template, request, redirect, url_for, flash, jsonify
from app import app, db
from models import Security, Transaction, PortfolioSnapshot
from portfolio_manager import PortfolioManager
from quant_engine import parse_price_csv, run_sma_backtest

@app.route('/')
def index():
    """Home page showing portfolio overview"""
    summary = PortfolioManager.get_portfolio_summary()
    metrics = PortfolioManager.calculate_portfolio_metrics()
    risk = PortfolioManager.calculate_risk_metrics()
    
    # Record today's snapshot if not already recorded
    PortfolioManager.record_snapshot()
    
    # Get latest securities
    latest_securities = Security.query.order_by(Security.created_at.desc()).limit(5).all()
    
    return render_template('index.html', 
                           summary=summary, 
                           metrics=metrics, 
                           risk=risk,
                           latest_securities=latest_securities)

@app.route('/portfolio')
def portfolio():
    """Page showing detailed portfolio"""
    securities = Security.query.all()
    summary = PortfolioManager.get_portfolio_summary()
    
    return render_template('portfolio.html', securities=securities, summary=summary)

@app.route('/portfolio/export.csv')
def export_portfolio_csv():
    """Download the current holdings in a portable CSV format."""
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['ticker', 'name', 'asset_class', 'purchase_price', 'current_price',
                     'quantity', 'purchase_date', 'sector', 'country', 'currency', 'notes'])
    for security in Security.query.order_by(Security.ticker.asc()).all():
        writer.writerow([
            security.ticker, security.name, security.asset_class, security.purchase_price,
            security.current_price, security.quantity, security.purchase_date.isoformat(),
            security.sector or '', security.country or '', security.currency or 'USD',
            security.notes or ''
        ])
    return Response(
        output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=meridian-portfolio.csv'}
    )

@app.route('/portfolio/import', methods=['GET', 'POST'])
def import_portfolio_csv():
    """Import holdings and their opening buy transactions from a validated CSV."""
    if request.method == 'GET':
        return render_template('import_csv.html')

    upload = request.files.get('file')
    if upload is None or not upload.filename:
        flash('Choose a CSV file to import.', 'warning')
        return redirect(url_for('import_portfolio_csv'))

    try:
        raw = upload.stream.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('The file is larger than the 2 MB limit.')
        text = raw.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text))
        normalized_headers = {str(header or '').strip().lower() for header in (reader.fieldnames or [])}
        required = {'ticker', 'name', 'asset_class', 'purchase_price', 'current_price', 'quantity', 'purchase_date'}
        missing = sorted(required - normalized_headers)
        if missing:
            raise ValueError('Missing required columns: ' + ', '.join(missing))

        field_map = {str(header).strip().lower(): header for header in (reader.fieldnames or [])}
        prepared = []
        errors = []
        for row_number, row in enumerate(reader, start=2):
            if row_number > 502:
                errors.append('Imports are limited to 500 holdings per file.')
                break
            values = {key: (row.get(original) or '').strip() for key, original in field_map.items()}
            try:
                ticker = values['ticker'].upper()
                name = values['name']
                asset_class = values['asset_class']
                purchase_price = float(values['purchase_price'])
                current_price = float(values['current_price'])
                quantity = float(values['quantity'])
                purchase_date = datetime.strptime(values['purchase_date'], '%Y-%m-%d').date()
                currency = (values.get('currency') or 'USD').upper()
                if not ticker or len(ticker) > 10 or not name or len(name) > 100:
                    raise ValueError('ticker or name is empty or too long')
                if not asset_class or len(asset_class) > 50:
                    raise ValueError('asset_class is empty or too long')
                if not all(math.isfinite(value) for value in (purchase_price, current_price, quantity)):
                    raise ValueError('prices and quantity must be finite numbers')
                if purchase_price <= 0 or current_price < 0 or quantity <= 0:
                    raise ValueError('prices must be nonnegative and quantity must be positive')
                if not re.fullmatch(r'[A-Z]{3}', currency):
                    raise ValueError('currency must be a 3-letter code')
                if currency != 'USD':
                    raise ValueError('This portfolio reports in USD. Convert prices before importing; FX conversion is not applied.')
                prepared.append({
                    'ticker': ticker, 'name': name, 'asset_class': asset_class,
                    'purchase_price': purchase_price, 'current_price': current_price,
                    'quantity': quantity, 'purchase_date': purchase_date,
                    'sector': values.get('sector', '')[:50] or None,
                    'country': values.get('country', '')[:50] or None,
                    'currency': currency, 'notes': values.get('notes', '') or None
                })
            except (KeyError, TypeError, ValueError) as error:
                errors.append(f'Row {row_number}: {error}')
                if len(errors) == 5:
                    break

        if errors:
            raise ValueError('Import stopped without saving. ' + ' | '.join(errors))
        if not prepared:
            raise ValueError('The CSV has no holdings to import.')

        for values in prepared:
            security = Security(**values)
            db.session.add(security)
            db.session.flush()
            db.session.add(Transaction(
                security_id=security.id,
                transaction_type='buy',
                price=values['purchase_price'],
                quantity=values['quantity'],
                transaction_date=values['purchase_date']
            ))
        db.session.commit()
        flash(f'Imported {len(prepared)} position(s).', 'success')
        return redirect(url_for('portfolio'))
    except (UnicodeDecodeError, csv.Error, ValueError) as error:
        db.session.rollback()
        flash(str(error), 'danger')
        return redirect(url_for('import_portfolio_csv'))
    except Exception:
        db.session.rollback()
        flash('The import could not be completed. No rows were saved.', 'danger')
        return redirect(url_for('import_portfolio_csv'))

@app.route('/portfolio/template.csv')
def download_import_template():
    """Download a blank CSV with the supported import columns."""
    output = io.StringIO(newline='')
    csv.writer(output).writerow([
        'ticker', 'name', 'asset_class', 'purchase_price', 'current_price',
        'quantity', 'purchase_date', 'sector', 'country', 'currency', 'notes'
    ])
    return Response(
        output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=meridian-import-template.csv'}
    )

@app.route('/research', methods=['GET', 'POST'])
def research_lab():
    """Run an in-memory, long-only SMA crossover backtest on uploaded closes."""
    result = None
    fast_window = 20
    slow_window = 50
    cost_bps = 5.0
    if request.method == 'POST':
        upload = request.files.get('prices')
        try:
            fast_window = int(request.form.get('fast_window', '20'))
            slow_window = int(request.form.get('slow_window', '50'))
            cost_bps = float(request.form.get('cost_bps', '5'))
            if upload is None or not upload.filename:
                raise ValueError('Choose a daily price CSV first.')
            raw = upload.stream.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('The price file is larger than the 2 MB limit.')
            prices = parse_price_csv(raw)
            result = run_sma_backtest(prices, fast_window, slow_window, cost_bps)
            result['fast_window'] = fast_window
            result['slow_window'] = slow_window
        except (ValueError, TypeError, OverflowError) as error:
            flash(f'Backtest not run: {error}', 'danger')

    return render_template(
        'research.html', result=result, fast_window=fast_window,
        slow_window=slow_window, cost_bps=cost_bps
    )

@app.route('/research/template.csv')
def research_price_template():
    """Download the required date/close CSV header for historical prices."""
    return Response(
        'date,close\n',
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=meridian-price-history.csv'}
    )

@app.route('/add_security', methods=['GET', 'POST'])
def add_security():
    """Page for adding a new security to the portfolio"""
    if request.method == 'POST':
        try:
            # Get form data
            ticker = request.form.get('ticker').upper()
            name = request.form.get('name')
            asset_class = request.form.get('asset_class')
            purchase_price = float(request.form.get('purchase_price'))
            current_price = float(request.form.get('current_price'))
            quantity = float(request.form.get('quantity'))
            purchase_date = datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d')
            sector = request.form.get('sector')
            country = request.form.get('country')
            currency = request.form.get('currency', 'USD')
            notes = request.form.get('notes')
            if currency.upper() != 'USD':
                raise ValueError('This portfolio reports in USD. Convert prices before adding a position; FX conversion is not applied.')
            
            # Create new security
            security = Security(
                ticker=ticker,
                name=name,
                asset_class=asset_class,
                purchase_price=purchase_price,
                current_price=current_price,
                quantity=quantity,
                purchase_date=purchase_date,
                sector=sector,
                country=country,
                currency=currency,
                notes=notes
            )
            
            db.session.add(security)
            db.session.commit()
            
            # Create transaction record
            transaction = Transaction(
                security_id=security.id,
                transaction_type='buy',
                price=purchase_price,
                quantity=quantity,
                transaction_date=purchase_date
            )
            
            db.session.add(transaction)
            db.session.commit()
            
            flash(f"Security {ticker} added successfully!", "success")
            return redirect(url_for('portfolio'))
        
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding security: {str(e)}", "danger")
    
    # Asset classes for dropdown
    asset_classes = [
        'Stock', 'ETF', 'Mutual Fund', 'Government Bonds', 'Corporate Bonds', 
        'Municipal Bonds', 'Cash', 'Cryptocurrency', 'Real Estate', 
        'Commodity', 'Options', 'Futures', 'Other'
    ]
    
    return render_template('add_security.html', asset_classes=asset_classes)

@app.route('/edit_security/<int:security_id>', methods=['GET', 'POST'])
def edit_security(security_id):
    """Page for editing an existing security"""
    security = Security.query.get_or_404(security_id)
    
    if request.method == 'POST':
        try:
            # Update security data
            security.name = request.form.get('name')
            security.asset_class = request.form.get('asset_class')
            security.current_price = float(request.form.get('current_price'))
            security.quantity = float(request.form.get('quantity'))
            security.sector = request.form.get('sector')
            security.country = request.form.get('country')
            security.currency = request.form.get('currency', 'USD')
            if security.currency.upper() != 'USD':
                raise ValueError('This portfolio reports in USD. Convert prices before saving; FX conversion is not applied.')
            security.notes = request.form.get('notes')
            security.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            flash(f"Security {security.ticker} updated successfully!", "success")
            return redirect(url_for('portfolio'))
        
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating security: {str(e)}", "danger")
    
    # Asset classes for dropdown
    asset_classes = [
        'Stock', 'ETF', 'Mutual Fund', 'Government Bonds', 'Corporate Bonds', 
        'Municipal Bonds', 'Cash', 'Cryptocurrency', 'Real Estate', 
        'Commodity', 'Options', 'Futures', 'Other'
    ]
    
    return render_template('add_security.html', asset_classes=asset_classes, security=security, edit=True)

@app.route('/delete_security/<int:security_id>', methods=['POST'])
def delete_security(security_id):
    """Route for deleting a security"""
    security = Security.query.get_or_404(security_id)
    
    try:
        # Delete associated transactions first
        Transaction.query.filter_by(security_id=security_id).delete()
        
        # Delete the security
        db.session.delete(security)
        db.session.commit()
        
        flash(f"Security {security.ticker} deleted successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting security: {str(e)}", "danger")
    
    return redirect(url_for('portfolio'))

@app.route('/analysis')
def analysis():
    """Page for displaying portfolio analysis"""
    summary = PortfolioManager.get_portfolio_summary()
    metrics = PortfolioManager.calculate_portfolio_metrics()
    risk = PortfolioManager.calculate_risk_metrics()
    
    return render_template('analysis.html', summary=summary, metrics=metrics, risk=risk)

@app.route('/reports')
def reports():
    """Page for generating and displaying portfolio reports"""
    report = PortfolioManager.generate_report()
    
    return render_template('reports.html', report=report)

@app.route('/api/portfolio_performance')
def portfolio_performance():
    """API endpoint for fetching portfolio performance data"""
    days = request.args.get('days', 30, type=int)
    performance = PortfolioManager.get_historical_performance(days)
    
    return jsonify(performance)

@app.route('/api/asset_allocation')
def asset_allocation():
    """API endpoint for fetching asset allocation data"""
    summary = PortfolioManager.get_portfolio_summary()
    
    return jsonify({
        'labels': list(summary['asset_allocation'].keys()),
        'data': list(summary['asset_allocation'].values())
    })

@app.route('/api/sector_allocation')
def sector_allocation():
    """API endpoint for fetching sector allocation data"""
    summary = PortfolioManager.get_portfolio_summary()
    
    return jsonify({
        'labels': list(summary['sector_allocation'].keys()),
        'data': list(summary['sector_allocation'].values())
    })

@app.route('/api/update_prices', methods=['POST'])
def update_prices():
    """API endpoint for updating current prices"""
    data = request.json
    
    try:
        for security_update in data:
            security_id = security_update.get('id')
            current_price = security_update.get('current_price')
            
            if security_id and current_price:
                security = Security.query.get(security_id)
                if security:
                    security.current_price = float(current_price)
                    security.updated_at = datetime.utcnow()
        
        db.session.commit()
        return jsonify({'status': 'success'})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)})

@app.errorhandler(404)
def page_not_found(e):
    """Custom 404 page"""
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    """Custom 500 page"""
    return render_template('500.html'), 500
