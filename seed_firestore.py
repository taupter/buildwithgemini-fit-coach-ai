from google.cloud import firestore
from google.api_core.exceptions import NotFound, PermissionDenied

# Hardcoded project ID as string for Firestore client and seed script
PROJECT_ID = "qwiklabs-gcp-03-5d4f25a91be7"
COLLECTION_NAME = "exercises"

db = firestore.Client(project=PROJECT_ID)

INITIAL_EXERCISES = [
    {
        "id": "ex_bench_press",
        "name": "Barbell Bench Press",
        "category": "Chest",
        "equipment": "Barbell",
        "difficulty": "Intermediate",
        "description": "Lie on a bench, lower barbell to chest level, and press upward until arms are extended."
    },
    {
        "id": "ex_goblet_squat",
        "name": "Goblet Squat",
        "category": "Legs",
        "equipment": "Kettlebell",
        "difficulty": "Beginner",
        "description": "Hold a kettlebell or dumbbell close to your chest, squat down keeping knees aligned with toes, and stand."
    },
    {
        "id": "ex_pull_ups",
        "name": "Pull-ups",
        "category": "Back",
        "equipment": "Bodyweight",
        "difficulty": "Intermediate",
        "description": "Grasp the pull-up bar with an overhand grip, pull your chest up to the bar, and lower smoothly."
    },
    {
        "id": "ex_dumbbell_press",
        "name": "Dumbbell Overhead Press",
        "category": "Shoulders",
        "equipment": "Dumbbell",
        "difficulty": "Beginner",
        "description": "Press dumbbells upward from shoulder height to full overhead lock-out while maintaining core tension."
    },
    {
        "id": "ex_romanian_deadlift",
        "name": "Romanian Deadlift",
        "category": "Legs",
        "equipment": "Barbell",
        "difficulty": "Intermediate",
        "description": "Hinge at the hips with knees slightly bent, lower bar along shins until hamstrings stretch, and drive hips forward."
    }
]

def seed_database():
    print(f"Seeding Firestore collection '{COLLECTION_NAME}' in project '{PROJECT_ID}'...")
    try:
        collection_ref = db.collection(COLLECTION_NAME)
        for exercise in INITIAL_EXERCISES:
            doc_id = exercise["id"]
            collection_ref.document(doc_id).set(exercise)
            print(f"  Added exercise: {exercise['name']} ({doc_id})")
        print("Firestore seeding completed successfully!")
    except (NotFound, PermissionDenied) as e:
        print(f"Firestore notice: {e}")

if __name__ == "__main__":
    seed_database()
