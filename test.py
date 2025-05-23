# test_models.py
from db import engine, SessionLocal
from models import Base, User, SavedSchedule, CourseCache

# 1) (Re)create all tables
Base.metadata.drop_all(bind=engine)    # start from a clean slate
Base.metadata.create_all(bind=engine)

# 2) Open a session
session = SessionLocal()

# 3) Add a user
u = User(
    email="alice@example.com", 
    name="Alice",
    oauth_id="test123"  # Added this line for the new required field
)
session.add(u)
session.commit()
print(f"Created User: id={u.id}, email={u.email}")

# 4) Add a schedule for that user
sched = SavedSchedule(
    user_id = u.id,
    term_id = 20253,
    name    = "Fall 2025 Plan",
    sections = [12345, 67890]
)
session.add(sched)
session.commit()
print(f"Created Schedule: id={sched.id}, sections={sched.sections}")

# 5) Add a cache entry
cache = CourseCache(
    term_id    = 20253,
    department = "CSCI",
    payload    = {"foo": "bar"}
)
session.add(cache)
session.commit()
got = session.query(CourseCache).first()
print("Cache row:", got.term_id, got.department, got.payload)

# 6) Query back everything
users = session.query(User).all()
print("All users:", users)
schedules = session.query(SavedSchedule).all()
print("All schedules:", [(s.id, s.sections) for s in schedules])
