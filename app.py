import os
from flask import Flask, redirect, url_for, session
from flask_cors import CORS
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from models import User
from db import SessionLocal

load_dotenv()

login_manager = LoginManager()
oauth = OAuth()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_app():
    app = Flask(__name__)
    
    # Ensure all required env vars are present
    required_vars = ["FLASK_SECRET_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
    for var in required_vars:
        if not os.environ.get(var):
            raise ValueError(f"Missing required environment variable: {var}")
    
    # Configure Flask app
    app.secret_key = os.environ["FLASK_SECRET_KEY"]
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    CORS(app, origins=["http://localhost:3000"])

    # Initialize extensions
    oauth.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"

    # OAuth setup
    oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile",
            "nonce": lambda: os.urandom(16).hex()  # Add nonce generator
        },
    )

    @login_manager.user_loader
    def load_user(user_id):
        db = next(get_db())
        try:
            return db.query(User).get(int(user_id))
        finally:
            db.close()

    @app.route("/")     
    def home():
        return "Welcome to USC Schedule Builder!"

    @app.route("/ping")
    def ping():
        return "pong"

    @app.route("/login")
    def login():
        # Store nonce in session
        nonce = os.urandom(16).hex()
        session['nonce'] = nonce
        redirect_uri = url_for("auth", _external=True)
        return oauth.google.authorize_redirect(redirect_uri, nonce=nonce)

    @app.route("/auth")
    def auth():
        token = oauth.google.authorize_access_token()
        user_info = oauth.google.parse_id_token(token, nonce=session['nonce'])
        # Extract fields
        oauth_id = user_info["sub"]
        email = user_info["email"]
        name = user_info.get("name", "")

        # Get database session
        db = next(get_db())
        
        # Find or create user
        user = db.query(User).filter_by(oauth_id=oauth_id).first()
        if not user:
            user = User(oauth_id=oauth_id, email=email, name=name)
            db.add(user)
            db.commit()

        login_user(user)
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        return f"Hello, {current_user.name}!"

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("home"))

    return app

if __name__ == "__main__":
    create_app().run(debug=True)
