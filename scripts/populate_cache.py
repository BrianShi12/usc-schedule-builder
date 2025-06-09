import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from uscschedule import Schedule

# Add parent directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import CourseCache

load_dotenv()

def populate_department_cache(department: str, term_id: int):
    engine = create_engine(os.getenv('DATABASE_URL'))
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        # Fetch data directly from USC API
        sched = Schedule()
        dept = sched.get_department(department, semester_id=term_id)
        courses = dept.courses

        # Format courses data
        courses_list = []
        for c in courses:
            course_dict = {
                "published_course_id": c.published_course_id,
                "scheduled_course_id": c.scheduled_course_id,
                "title": c.title,
                "units": c.units,
                "description": c.description,
                "sections": [
                    {
                        "id": s.id,
                        "session": s.session,
                        "type": s.type,
                        "capacity": s.capacity,
                        "registered": s.registered,
                        "wait_quantity": s.wait_quantity,
                        "day": s.day,
                        "start_time": s.start_time,
                        "end_time": s.end_time,
                        "location": s.location,
                        "instructors": [
                            {
                                "first_name": instructor.first_name,
                                "last_name": instructor.last_name,
                            }
                            for instructor in s.instructors
                        ]
                    }
                    for s in c.sections
                ]
            }
            courses_list.append(course_dict)

        # Format data as dictionary structure
        cache_data = {
            "department": department,
            "term_id": term_id,
            "courses": courses_list
        }

        # Check if entry exists
        existing = db.query(CourseCache).filter_by(
            department=department,
            term_id=term_id
        ).first()

        # Store reference to cache entry
        if existing:
            print(f"Updating existing {department} cache...")
            existing.payload = cache_data
            cache_entry = existing
        else:
            print(f"Creating new {department} cache entry...")
            cache_entry = CourseCache(
                department=department,
                term_id=term_id,
                payload=cache_data
            )
            db.add(cache_entry)

        db.commit()
        print(f"\n=== Cache Data Structure for {department} ===")
        print(f"Total courses cached: {len(courses_list)}")
        
        print("\nAll cached course IDs:")
        for course in courses_list:
            print(f"- {course['published_course_id']}")
        
        print("\n✅ Cache populated successfully!")
    
    except Exception as e:
        print(f"❌ Error for {department}: {str(e)}")
        db.rollback()
    finally:
        db.close()

def populate_all_departments():
    # List of all department codes
    departments = [
        "ACCT", "ACMD", "ADSC", "AHIS", "ALI", "AME", "AMST", "ANST", "ANTH", 
        "ARAB", "ARCG", "ARCH", "ART", "ARTL", "ASTR", "ASTE", "BAEP", "BIOC", 
        "BISC", "BKN", "BME", "BPMK", "BPSI", "BUAD", "BUCO", "CE", "CBG", "CBY", 
        "CGSC", "CHE", "CHEM", "CJ", "CLAS", "CMGT", "CMPP", "CNTV", "COH", "COLT", 
        "COMM", "CORE", "CRIT", "CSCI", "CSLC", "CTAN", "CTCS", "CTIN", "CTPR", 
        "CTWR", "CXPT", "DANC", "DCL", "DENT", "DES", "DHIS", "DMM", "DSM", "DSO", 
        "DSR", "EALC", "EASC", "ECON", "EDCO", "EDHP", "EDUC", "EDUE", "EE", "EIS", 
        "EM", "ENE", "ENGL", "ENGR", "ENST", "FBE", "FDN", "FREN", "FSEM", "GDEN", 
        "GEOL", "GERM", "GERO", "GESM", "GPG", "GPH", "GR", "GRSC", "GSBA", "GSEC", 
        "HBIO", "HCDA", "HEBR", "HIST", "HMGT", "HP", "HRM", "HT", "IAS", "IDSN", 
        "IML", "INDS", "INTD", "IR", "IRAN", "ISE", "ITAL", "ITP", "JOUR", "JS", 
        "KSI", "LAT", "LAW", "LIM", "LING", "MASC", "MATH", "MBPH", "MDA", "MDED", 
        "MDES", "MED", "MEDB", "MEDS", "MICB", "MKT", "MOR", "MPHY", "MPTX", "MS", 
        "MSCR", "MTEC", "MTAL", "MUCM", "MUCD", "MUCO", "MUEN", "MUHL", "MUIN", 
        "MUJZ", "MUSC", "NAUT", "NEUR", "NIIN", "NSC", "NSCI", "NURS", "OFP", "OFPM", 
        "OHNS", "OPR", "OS", "OT", "PAIN", "PATH", "PBHS", "PDF", "PHED", "PHBI", 
        "PHIL", "PHRD", "PHYS", "PJMT", "PM", "PMEP", "POIR", "PORT", "POSC", "PPD", 
        "PPDE", "PR", "PRIN", "PRSM", "PSCI", "PSYC", "PT", "PTE", "PUBD", "QBIO", 
        "RED", "REL", "RISK", "RNR", "RUSS", "RXRS", "SAE", "SCOR", "SCRM", "SGY", 
        "SLL", "SMGT", "SOCI", "SPAN", "SSCI", "SSEM", "SWKC", "SWKO", "SWMS", "TAC", 
        "THTE", "THTR", "TRGN", "USC", "VISS", "WRIT"
    ]

    term_id = 20253  # Fall 2025

    for dept in departments:
        print(f"\nProcessing department: {dept}")
        populate_department_cache(dept, term_id)

if __name__ == "__main__":
    populate_all_departments()