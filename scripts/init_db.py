from app.database import DATABASE_PATH, initialize_database


if __name__ == "__main__":
    initialize_database()
    print(DATABASE_PATH)

