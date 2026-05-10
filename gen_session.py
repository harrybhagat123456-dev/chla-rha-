import asyncio
import os
from pyrogram import Client

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")

async def main():
    print("\n" + "="*50)
    print("   🔑  PYROGRAM SESSION STRING GENERATOR")
    print("="*50)
    print(f"\n✅ API_ID   : {API_ID}  (from secrets)")
    print(f"✅ API_HASH : {API_HASH[:6]}{'*'*(len(API_HASH)-6)}  (from secrets)")
    print("\nPyrogram will now guide you through login.\n")

    app = Client("gen_tmp", api_id=API_ID, api_hash=API_HASH, in_memory=True)

    await app.start()
    session_string = await app.export_session_string()
    await app.stop()

    print("\n" + "="*50)
    print("✅  YOUR SESSION STRING:")
    print("="*50)
    print(f"\n{session_string}\n")
    print("="*50)
    print("\n📋 Copy the string above and add it as:")
    print("   Replit Secrets → Name: SESSION → paste it\n")

asyncio.run(main())
