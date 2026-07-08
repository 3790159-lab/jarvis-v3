import sys, asyncio
sys.path.insert(0, r"C:\jarvis")
import os
os.chdir(r"C:\jarvis")

from app.services.block_m_common.persona_storage import PersonaStorage

async def main():
    s = PersonaStorage()
    personas = await s.list_personas()
    print(f"Total: {len(personas)}")
    for p in personas:
        print(f"  {p.persona_id}  |  name={p.name!r}")

asyncio.run(main())
