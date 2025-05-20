import os
from flask import Flask
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-key")
    CORS(app, origins=["http://localhost:3000"])
    @app.route("/ping")
    def ping():
        return "pong"
    return app

if __name__ == "__main__":
    create_app().run(debug=True)
