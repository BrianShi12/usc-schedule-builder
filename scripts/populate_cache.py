import sys
import os
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Add parent directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Now we can import from parent directory
from models import CourseCache

load_dotenv()

def populate_csci_cache():
    engine = create_engine(os.getenv('DATABASE_URL'))
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        # Read JSON file
        with open('/Users/brianscomputer/usc-schedule-poc/CSCI_20251.json', 'r') as file:
            courses_list = json.load(file)  # This loads as a list

        # Format data as dictionary structure
        cache_data = {
            "department": "CSCI",
            "term_id": 20251,
            "courses": courses_list  # The list becomes value of "courses" key
        }

        # Check if entry exists
        existing = db.query(CourseCache).filter_by(
            department="CSCI",
            term_id=20251
        ).first()

        # Store reference to cache entry
        if existing:
            print("Updating existing CSCI cache...")
            existing.payload = cache_data
            cache_entry = existing
        else:
            print("Creating new CSCI cache entry...")
            cache_entry = CourseCache(
                department="CSCI",
                term_id=20251,
                payload=cache_data
            )
            db.add(cache_entry)

        db.commit()
        print("\n=== Cache Data Structure ===")
        print(f"Total courses cached: {len(cache_data['courses'])}")
        
        # Print all cached course IDs from cache_data instead
        print("\nAll cached course IDs:")
        for course in cache_data['courses']:
            print(f"- {course['published_course_id']}")
        
        print("\n✅ Cache populated successfully!")
    
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    populate_csci_cache()