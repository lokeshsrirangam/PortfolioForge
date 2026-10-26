from app import app  # noqa: F401
import routes  # noqa: F401

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)
