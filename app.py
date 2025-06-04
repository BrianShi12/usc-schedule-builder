import os
from flask import Flask, redirect, url_for, session, jsonify, request
from flask_cors import CORS
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from models import User, SavedSchedule, CourseCache
from db import SessionLocal
from itertools import combinations, product
from typing import List, Dict
from datetime import datetime, time

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
    CORS(app, 
         origins=["http://localhost:3000", "http://127.0.0.1:3000"],
         supports_credentials=True,
         allow_headers=["Content-Type", "Accept"],
         methods=["GET", "POST", "PUT", "DELETE"])

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
        return redirect("http://localhost:3000")

    @app.route("/dashboard")
    @login_required
    def dashboard():
        db = next(get_db())
        try:
            schedules = db.query(SavedSchedule)\
                         .filter_by(user_id=current_user.id)\
                         .all()
            return jsonify({
                "user": {
                    "name": current_user.name,
                    "email": current_user.email
                },
                "schedules": [{
                    "id": s.id,
                    "name": s.name,
                    "term_id": s.term_id,
                    "sections": s.sections
                } for s in schedules]
            })
        finally:
            db.close()

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        session.clear()
        return jsonify({"message": "Logged out successfully"})  # Return JSON instead of redirect

    def parse_time(time_str: str) -> time:
        """Convert time string like '14:00' to datetime.time object"""
        if not time_str or time_str == "TBA":
            return None
        return datetime.strptime(time_str, '%H:%M').time()

    def parse_days(day: str) -> list:
        """Convert day string like 'MW' to list of days"""
        if not day:
            return []
        # Map single-letter days to full representation    
        day_map = {
            'M': 'Monday',
            'T': 'Tuesday',
            'W': 'Wednesday',
            'H': 'Thursday',
            'F': 'Friday'
        }
        return [day_map[d] for d in str(day)]

    def has_time_conflict(section1: dict, section2: dict) -> bool:
        """Check if two sections have overlapping times"""
        # Handle TBA times
        if not section1.get('start_time') or not section2.get('start_time'):
            return False
            
        # Get days as lists
        days1 = parse_days(section1['day'])
        days2 = parse_days(section2['day'])
        
        # Check for overlapping days
        common_days = set(days1) & set(days2)
        if not common_days:
            return False
            
        # Check times on overlapping days
        time1_start = parse_time(section1['start_time'])
        time1_end = parse_time(section1['end_time'])
        time2_start = parse_time(section2['start_time'])
        time2_end = parse_time(section2['end_time'])
        
        return (time1_start <= time2_end) and (time2_start <= time1_end)

    def get_sections_from_cache(db_session, term_id: int, courses: List[str]) -> Dict[str, List[Dict]]:
        """Extract section data from CourseCache JSONB payload"""
        sections_by_course = {}
        
        for course in courses:
            # Split course into department and number (e.g. "CSCI-570" -> "CSCI")
            department = course.split('-')[0]
            
            # Get cached data
            cache_entry = db_session.query(CourseCache).filter_by(
                term_id=term_id,
                department=department
            ).first()
            
            if not cache_entry:
                continue
                
            # Extract sections for this specific course from the department payload
            course_data = next(
                (c for c in cache_entry.payload['courses'] 
                 if c['course_id'] == course),
                None
            )
            
            if course_data:
                sections_by_course[course] = course_data['sections']
        
        return sections_by_course

    def get_sections_by_type(course_data: dict) -> dict:
        """Group sections by type (Lec, Lab, Dis, etc)"""
        sections_by_type = {}
        for section in course_data['sections']:
            section_type = section['type']
            if section_type not in sections_by_type:
                sections_by_type[section_type] = []
            sections_by_type[section_type].append(section)
        return sections_by_type

    def generate_diverse_schedules(courses_data: list, max_schedules: int = 15) -> list:
        """Generate schedules prioritizing different lecture combinations"""
        all_valid_schedules = []
        seen_lecture_combinations = set()
        
        # Get all lecture sections for each course
        course_lectures = {}
        course_discussions = {}
        
        for course in courses_data:
            sections = get_sections_by_type(course)
            course_id = course['published_course_id']
            course_lectures[course_id] = sections.get('Lec', [])
            # Combine discussions/labs/quiz sections
            other_sections = []
            for type_ in ['Dis', 'Lab', 'Qz']:
                other_sections.extend(sections.get(type_, []))
            course_discussions[course_id] = other_sections

        # Generate all possible lecture combinations
        lecture_combinations = list(product(*[
            lectures for lectures in course_lectures.values() if lectures
        ]))
        
        # Randomize combinations for variety
        import random
        random.shuffle(lecture_combinations)
        
        # Try each lecture combination
        for lecture_combo in lecture_combinations:
            if len(all_valid_schedules) >= max_schedules:
                break
                
            # Skip if we've seen this lecture combination
            lecture_key = tuple(sorted(lec['id'] for lec in lecture_combo))
            if lecture_key in seen_lecture_combinations:
                continue
                
            # Check for conflicts between lectures
            has_conflict = False
            for i, lec1 in enumerate(lecture_combo):
                for lec2 in lecture_combo[i+1:]:
                    if has_time_conflict(lec1, lec2):
                        has_conflict = True
                        break
                if has_conflict:
                    break
                    
            if has_conflict:
                continue
                
            # Find valid discussion/lab combinations
            current_schedule = list(lecture_combo)
            seen_lecture_combinations.add(lecture_key)
            
            # Add required discussions/labs
            for course_id, discussions in course_discussions.items():
                for disc in discussions:
                    # Check if discussion conflicts with any current sections
                    valid = True
                    for section in current_schedule:
                        if has_time_conflict(disc, section):
                            valid = False
                            break
                    if valid:
                        current_schedule.append(disc)
                        break  # Only add one discussion per course
            
            all_valid_schedules.append(current_schedule)
            
        return all_valid_schedules[:max_schedules]

    @app.route("/schedules/generate", methods=["POST"])
    @login_required
    def generate_schedules():
        data = request.get_json()
        course_ids = data.get("courses")  # e.g. ["CSCI-570", "CSCI-585"]
        
        # Get course data from cache
        db = next(get_db())
        courses_data = []
        
        try:
            for course_id in course_ids:
                dept = course_id.split('-')[0]
                cache_entry = db.query(CourseCache)\
                               .filter_by(term_id=data["term_id"], 
                                        department=dept)\
                               .first()
                if cache_entry:
                    course_data = next(
                        (c for c in cache_entry.payload 
                         if c["published_course_id"] == course_id),
                        None
                    )
                    if course_data:
                        courses_data.append(course_data)
    
            schedules = generate_diverse_schedules(courses_data)
            
            return jsonify({
                "count": len(schedules),
                "schedules": schedules
            })
        finally:
            db.close()

    @app.route("/schedules/save", methods=["POST"])
    @login_required
    def save_generated_schedule():
        """Save a generated schedule"""
        db = next(get_db())
        try:
            data = request.get_json()
            schedule = SavedSchedule(
                user_id=current_user.id,
                term_id=data["term_id"],
                name=data.get("name", "My Schedule"),  # Default name if none provided
                sections=data["sections"]  # List of CRNs
            )
            db.add(schedule)
            db.commit()
            
            return jsonify({
                "id": schedule.id,
                "name": schedule.name,
                "sections": schedule.sections
            })
        finally:
            db.close()

    @app.route("/schedules/", methods=["GET"])
    @login_required
    def list_saved_schedules():
        """List all saved schedules for current user"""
        db = next(get_db())
        try:
            schedules = db.query(SavedSchedule)\
                         .filter_by(user_id=current_user.id)\
                         .all()
            return jsonify([{
                "id": s.id,
                "name": s.name,
                "term_id": s.term_id,
                "sections": s.sections
            } for s in schedules])
        finally:
            db.close()

    @app.route("/schedules/<int:schedule_id>", methods=["GET"])
    @login_required
    def get_schedule_detail(schedule_id):
        """Get detailed information about a specific schedule"""
        db = next(get_db())
        try:
            # Get the schedule
            schedule = db.query(SavedSchedule)\
                        .filter_by(id=schedule_id, user_id=current_user.id)\
                        .first()
            
            if not schedule:
                return jsonify({"error": "Schedule not found"}), 404
                
            # Get full details for each section in the schedule
            section_details = []
            for section_id in schedule.sections:
                # Find section in course cache
                cache_entry = db.query(CourseCache)\
                              .filter_by(term_id=schedule.term_id)\
                              .first()
                
                if cache_entry:
                    # Search through cached courses for this section
                    for course in cache_entry.payload:
                        for section in course.get("sections", []):
                            if section["id"] == str(section_id):
                                section_details.append({
                                    "crn": section["id"],
                                    "course_id": course["published_course_id"],
                                    "title": course["title"],
                                    "type": section["type"],
                                    "days": section["day"],
                                    "start_time": section["start_time"],
                                    "end_time": section["end_time"],
                                    "location": section["location"],
                                    "instructors": section["instructors"]
                                })
            
            return jsonify({
                "id": schedule.id,
                "name": schedule.name,
                "term_id": schedule.term_id,
                "sections": section_details,
                "total_units": sum(float(c["units"].split(",")[0]) 
                                 for c in cache_entry.payload 
                                 if any(s["id"] in schedule.sections 
                                       for s in c["sections"]))
            })
        finally:
            db.close()

    return app

if __name__ == "__main__":
    create_app().run(debug=True)
