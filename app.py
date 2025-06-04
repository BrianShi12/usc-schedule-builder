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
        
        # Debug logging
        print("\n=== Schedule Generation Debug ===")
        print(f"Received {len(courses_data)} courses")
        
        # Get all lecture sections for each course
        course_lectures = {}
        course_discussions = {}
        
        for course in courses_data:
            print(f"\nProcessing {course['published_course_id']}:")
            sections = get_sections_by_type(course)
            print(f"Sections by type: {dict((k, len(v)) for k, v in sections.items())}")
            
            course_id = course['published_course_id']
            course_lectures[course_id] = sections.get('Lec', [])
            print(f"Found {len(course_lectures[course_id])} lecture sections")
            
            # Combine discussions/labs/quiz sections
            other_sections = []
            for type_ in ['Dis', 'Lab', 'Qz']:
                sections_of_type = sections.get(type_, [])
                other_sections.extend(sections_of_type)
                print(f"Found {len(sections_of_type)} {type_} sections")
                
            course_discussions[course_id] = other_sections
            print(f"Total other sections: {len(course_discussions[course_id])}")

        # Generate all possible lecture combinations
        lecture_combinations = list(product(*[
            lectures for lectures in course_lectures.values() if lectures
        ]))
        
        print(f"\nGenerated {len(lecture_combinations)} possible lecture combinations")
        
        # Try each lecture combination
        for idx, lecture_combo in enumerate(lecture_combinations):
            if len(all_valid_schedules) >= max_schedules:
                break
                
            print(f"\nTrying combination {idx + 1}:")
            print("Lectures:", [f"{lec['type']} {lec['id']}" for lec in lecture_combo])
            
            # Skip if we've seen this lecture combination
            lecture_key = tuple(sorted(lec['id'] for lec in lecture_combo))
            if lecture_key in seen_lecture_combinations:
                print("Skipping duplicate lecture combination")
                continue
                
            # Check for conflicts between lectures
            has_conflict = False
            for i, lec1 in enumerate(lecture_combo):
                for lec2 in lecture_combo[i+1:]:
                    if has_time_conflict(lec1, lec2):
                        print(f"Conflict found between {lec1['id']} and {lec2['id']}")
                        has_conflict = True
                        break
                if has_conflict:
                    break
                    
            if has_conflict:
                continue
                
            # Find valid discussion/lab combinations
            current_schedule = list(lecture_combo)
            seen_lecture_combinations.add(lecture_key)
            
            print("Adding discussions/labs...")
            # Add required discussions/labs
            for course_id, discussions in course_discussions.items():
                for disc in discussions:
                    # Check if discussion conflicts with any current sections
                    valid = True
                    for section in current_schedule:
                        if has_time_conflict(disc, section):
                            print(f"Discussion {disc['id']} conflicts with {section['id']}")
                            valid = False
                            break
                    if valid:
                        print(f"Added {disc['type']} {disc['id']}")
                        current_schedule.append(disc)
                        break  # Only add one discussion per course
            
            print(f"Schedule {len(all_valid_schedules) + 1} complete with {len(current_schedule)} sections")
            all_valid_schedules.append(current_schedule)
            
        return all_valid_schedules[:max_schedules]

    @app.route("/schedules/generate", methods=["POST"])
    @login_required
    def generate_schedules():
        try:
            print("\n=== STARTING SCHEDULE GENERATION ===")
            data = request.get_json()
            course_ids = data.get("courses", [])
            term_id = data.get("term_id")
            
            print(f"Raw request data: {data}")
            print(f"Processing courses: {course_ids}")
            print(f"Term ID: {term_id}")
            
            if not course_ids:
                print("❌ No courses provided in request")
                return jsonify({"error": "No courses provided"}), 400
                
            db = next(get_db())
            schedules_data = []
            
            # Print database connection status
            print("✓ Database connection established")
            
            for course_id in course_ids:
                dept = course_id.split('-')[0]
                print(f"\nLooking up course: {course_id}")
                
                # Query the cache
                cache_entry = db.query(CourseCache)\
                               .filter_by(term_id=term_id, department=dept)\
                               .first()
                
                if not cache_entry:
                    print(f"❌ No cache entry found for department: {dept}")
                    continue
                    
                print(f"✓ Found cache entry for {dept}")
                print(f"Cache payload structure:")
                print(f"- Keys: {list(cache_entry.payload.keys())}")
                print(f"- Number of courses: {len(cache_entry.payload['courses'])}")
                print(f"- Looking for course_id: {course_id}")

                # Find specific course
                course_data = next(
                    (c for c in cache_entry.payload['courses']
                     if c['published_course_id'] == course_id),
                    None
                )
                
                if course_data:
                    print(f"✓ Found course data for {course_id}")
                    print(f"Course data keys: {list(course_data.keys())}")
                    if 'sections' not in course_data:
                        print(f"❌ No sections found for {course_id}")
                        continue
                        
                    print(f"Found {len(course_data['sections'])} sections:")
                    for section in course_data['sections']:
                        print(f"- {section['type']} {section['id']}: "
                              f"{section.get('day', 'No day')} "
                              f"{section.get('start_time', 'No time')}-"
                              f"{section.get('end_time', 'No time')}")
                    
                    schedules_data.append(course_data)

            if not schedules_data:
                print("❌ No valid courses found in cache")
                return jsonify({"error": "No valid courses found"}), 404
            
            print(f"\n✓ Found data for {len(schedules_data)} courses")
            print("Generating schedules...")
            
            generated_schedules = generate_diverse_schedules(schedules_data)
            print(f"✓ Generated {len(generated_schedules)} possible schedules")
            
            response_data = {
                "count": len(generated_schedules),
                "schedules": generated_schedules
            }
            print("\n=== SCHEDULE GENERATION COMPLETE ===")
            return jsonify(response_data)
                
        except Exception as e:
            print(f"❌ Error in schedule generation: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
        finally:
            if 'db' in locals():
                db.close()
                print("✓ Database connection closed")

    @app.route("/schedules/save", methods=["POST"])
    @login_required
    def save_generated_schedule():
        """Save a generated schedule"""
        db = next(get_db())
        try:
            data = request.get_json()
            print("Save schedule request data:", data)
            
            if not data or "sections" not in data:
                return jsonify({"error": "No sections provided"}), 400

            # Save schedule with section IDs directly
            schedule = SavedSchedule(
                user_id=current_user.id,
                term_id=data.get("term_id", 20251),
                name=data.get("name", "My Schedule"),
                sections=data["sections"]  # sections are already IDs
            )
            db.add(schedule)
            db.commit()
            
            # Return response with basic info
            return jsonify({
                "id": schedule.id,
                "name": schedule.name,
                "term_id": schedule.term_id,
                "sections": schedule.sections
            })
        except Exception as e:
            print(f"Error saving schedule: {str(e)}")
            db.rollback()
            return jsonify({"error": "Failed to save schedule"}), 500
        finally:
            db.close()

    @app.route("/schedules/", methods=["GET"])
    @login_required
    def list_saved_schedules():
        db = next(get_db())
        try:
            schedules = db.query(SavedSchedule)\
                         .filter_by(user_id=current_user.id)\
                         .all()
        
            response_data = []
            for schedule in schedules:
                print(f"Processing schedule {schedule.id} with sections: {schedule.sections}")
                
                # Get the cache entry for this term
                cache_entry = db.query(CourseCache)\
                               .filter_by(term_id=schedule.term_id, department="CSCI")\
                               .first()
                
                if not cache_entry:
                    print(f"No cache entry found for term {schedule.term_id}")
                    continue
                
                # Find full section details
                section_details = []
                for section_id in schedule.sections:
                    section_id_str = str(section_id)  # Convert to string for comparison
                    print(f"Looking for section: {section_id_str}")
                    
                    for course in cache_entry.payload['courses']:
                        for section in course.get('sections', []):
                            if str(section['id']) == section_id_str:  # Compare strings
                                print(f"Found section {section_id} in {course['published_course_id']}")
                                section_details.append({
                                    'id': section['id'],
                                    'type': section['type'],
                                    'day': section['day'],
                                    'start_time': section['start_time'],
                                    'end_time': section['end_time'],
                                    'location': section.get('location', 'TBA'),
                                    'instructors': section.get('instructors', [])
                                })
                                break
            
                formatted_schedule = {
                    "id": schedule.id,
                    "name": schedule.name,
                    "term_id": schedule.term_id,
                    "sections": section_details  # This will now contain the full section details
                }
                print(f"Formatted schedule with {len(section_details)} sections")
                response_data.append(formatted_schedule)
                
            return jsonify(response_data)
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
