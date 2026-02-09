import asyncio
from dotenv import load_dotenv
load_dotenv()
from src.database import db

async def main():
    print("Seeding menu...")
    await db.seed_menu()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(main())
