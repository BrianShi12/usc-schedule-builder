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
import random

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
            return False  # Treat TBA sections as non-conflicting
        
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
        """Extract section data from CourseCache JSONB payload, excluding TBA sections."""
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
                 if c['published_course_id'] == course),
                None
            )
            
            if course_data:
                # Filter out TBA sections
                valid_sections = [
                    section for section in course_data['sections']
                    if section.get('start_time') and section.get('day')  # Exclude TBA sections
                ]
                
                sections_by_course[course] = valid_sections
    
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
        """Generate schedules prioritizing different lecture combinations with randomness."""
        all_valid_schedules = []
        seen_combinations = set()
        failed_combinations = set()  # Track lecture combinations that fail repeatedly

        print("\n=== Schedule Generation Debug ===")
        print(f"Received {len(courses_data)} courses")

        # Group sections by course and type
        course_sections = {}
        lecture_combinations = []

        for course in courses_data:
            course_id = course['published_course_id']
            sections = get_sections_by_type(course)
            
            # Add course_id to each section
            for section_type in sections:
                for section in sections[section_type]:
                    section['course_id'] = course_id
                
            course_sections[course_id] = {
                'Lec': sections.get('Lec', []),
                'Dis': sections.get('Dis', []),
                'Lab': sections.get('Lab', []),
                'Qz': sections.get('Qz', [])
            }
            lecture_combinations.append(course_sections[course_id]['Lec'])

        # Generate all possible lecture combinations
        all_lecture_combinations = list(product(*lecture_combinations))
        print(f"\nGenerated {len(all_lecture_combinations)} lecture combinations")

        # Shuffle lecture combinations for randomness
        random.shuffle(all_lecture_combinations)

        # Cycle through lecture combinations evenly
        lecture_index = 0
        while len(all_valid_schedules) < max_schedules and lecture_index < len(all_lecture_combinations):
            lecture_combo = all_lecture_combinations[lecture_index]
            lecture_index += 1  # Move to the next combination

            # Skip failed combinations
            lecture_combo_key = tuple(lec['id'] for lec in lecture_combo)
            if lecture_combo_key in failed_combinations:
                print(f"Skipping previously failed lecture combination: {lecture_combo_key}")
                continue

            print("\nTrying lecture combination:")
            for lec in lecture_combo:
                print(f"- Lec {lec['id']}: {lec.get('day', 'TBA')} {lec.get('start_time', 'TBA')}-{lec.get('end_time', 'TBA')}")

            # Check if lectures conflict
            has_conflict = False
            for i, lec1 in enumerate(lecture_combo):
                for lec2 in lecture_combo[i+1:]:
                    if has_time_conflict(lec1, lec2):
                        has_conflict = True
                        break

            if has_conflict:
                print("Lecture combination has conflicts, skipping")
                failed_combinations.add(lecture_combo_key)  # Mark combination as failed
                continue

            # Generate multiple schedules for the same lecture combination
            schedule_attempts = 0
            for _ in range(3):  # Try up to 3 variations per lecture combination
                if len(all_valid_schedules) >= max_schedules:
                    break

                # Start with lectures
                current_schedule = list(lecture_combo)

                # Add other sections for each course
                schedule_valid = True
                for course_id, sections in course_sections.items():
                    # Get the lecture for this course
                    course_lecture = next((lec for lec in lecture_combo if lec in sections['Lec']), None)
                    if not course_lecture:
                        continue

                    # Try adding discussions, labs, quizzes - FIXED ORDER
                    for section_type in ['Dis', 'Lab', 'Qz']:
                        if sections[section_type]:
                            # Add required sections unconditionally
                            random_sections = random.sample(sections[section_type], len(sections[section_type]))
                            section_added = False
                            for section in random_sections:
                                if not any(has_time_conflict(section, existing) for existing in current_schedule):
                                    current_schedule.append(section)  # Add the section
                                    section_added = True
                                    break

                            # Only mark invalid if this section type is required
                            if not section_added and section_type in ['Dis']:  # Discussion sections are required
                                schedule_valid = False
                                break

                if schedule_valid:
                    schedule_key = tuple(sorted(section['id'] for section in current_schedule))
                    if schedule_key not in seen_combinations:
                        seen_combinations.add(schedule_key)
                        all_valid_schedules.append(current_schedule)
                        print(f"\n✓ Found valid schedule {len(all_valid_schedules)}:")
                        for section in current_schedule:
                            print(f"- {section['type']} {section['id']}: {section.get('day', 'TBA')} {section.get('start_time', 'TBA')}-{section.get('end_time', 'TBA')}")
                schedule_attempts += 1

        # If no valid schedules were generated for this combination, mark it as failed
        if schedule_attempts == 0:
            print(f"Lecture combination failed: {lecture_combo_key}")
            failed_combinations.add(lecture_combo_key)

        print(f"\nGenerated {len(all_valid_schedules)} valid schedules")
        return all_valid_schedules
    
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
            print("✓ Database connection established")
            
            # Use get_sections_from_cache to retrieve course data
            sections_by_course = get_sections_from_cache(db, term_id, course_ids)
            
            if not sections_by_course:
                print("❌ No valid courses found in cache")
                return jsonify({"error": "No valid courses found"}), 404
            
            print(f"\n✓ Found data for {len(sections_by_course)} courses")
            print("Generating schedules...")
            
            # Convert sections_by_course to the format expected by generate_diverse_schedules
            schedules_data = [
                {
                    "published_course_id": course_id,
                    "sections": sections
                }
                for course_id, sections in sections_by_course.items()
            ]
            
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

            # Extract just the section IDs from the section objects
            section_ids = [int(section['id']) for section in data['sections']]
            
            # Save schedule with section IDs
            schedule = SavedSchedule(
                user_id=current_user.id,
                term_id=data.get("term_id", 20251),
                name=data.get("name", "My Schedule"),
                sections=section_ids  # Now just an array of integers
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
                cache_entry = db.query(CourseCache)\
                               .filter_by(term_id=schedule.term_id, department="CSCI")\
                               .first()
                
                if not cache_entry:
                    continue
                
                # Reset section_details for each schedule
                section_details = []
                for section_id in schedule.sections:
                    section_id_str = str(section_id)
                    for course in cache_entry.payload['courses']:
                        for section in course.get('sections', []):
                            if str(section['id']) == section_id_str:
                                section_details.append({
                                    'id': section['id'],
                                    'type': section['type'],
                                    'day': section['day'],
                                    'start_time': section['start_time'],
                                    'end_time': section['end_time'],
                                    'location': section.get('location', 'TBA'),
                                    'instructors': section.get('instructors', []),
                                    'course_id': course['published_course_id']
                            })
                            # Remove break to ensure we find all sections

                formatted_schedule = {
                    "id": schedule.id,
                    "name": schedule.name,
                    "term_id": schedule.term_id,
                    "sections": section_details
                }
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
