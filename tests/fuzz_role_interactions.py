"""Randomized one-night interaction fuzz for Mafia Optimisma v3."""
from __future__ import annotations
import asyncio, random, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_core import FakeBot, FakeStorage, Settings, GameEngine, GameState, NightAction, Phase, PlayerState, generate_roles, store
from simulate_games import build_actions
from mafia_optimisma.content import ROLES

SETTINGS=Settings('x',registration_seconds=999,registration_warning_seconds=30,night_seconds=999,discussion_seconds=999,nomination_seconds=999,verdict_seconds=999)

async def one(seed:int, mode:str, count:int):
    random.seed(seed)
    storage=FakeStorage(); bot=FakeBot(); engine=GameEngine(SETTINGS,storage)
    g=GameState(3_000_000+seed,f'role-fuzz-{seed}',mode=mode,phase=Phase.NIGHT,day=random.randint(1,6))
    roles=generate_roles(mode,count)
    for i,role in enumerate(roles,1):
        g.players[i]=PlayerState(i,f'P{i}',number=i,role_key=role,initial_role_key=role)
    store.games[g.chat_id]=g
    # Seed some cross-night state.
    for p in g.players.values():
        if p.role_key=='surgeon' and random.random()<.35:
            p.self_heals_used=1
        if random.random()<.08:
            p.swapped_once=True
    g.actions=build_actions(g)
    before_alive={p.user_id:p.alive for p in g.players.values()}
    deaths,events=await engine.resolve_night(bot,g)
    death_ids=[p.user_id for p,_ in deaths]
    assert len(death_ids)==len(set(death_ids)), (seed,'duplicate deaths',death_ids)
    assert set(g.players)==set(range(1,count+1)), (seed,'player map changed')
    for p in g.players.values():
        assert p.role_key in ROLES, (seed,'invalid role',p.role_key)
        assert p.self_heals_used in {0,1}, (seed,'self heal counter',p.user_id,p.self_heals_used)
        if before_alive[p.user_id] is False:
            assert p.alive is False, (seed,'resurrection',p.user_id)
        if p.bodyguard_saved_id is not None:
            assert p.bodyguard_saved_id in g.players, (seed,'bad bodyguard target')
    assert all(isinstance(x,str) for x in events)
    engine.cancel_timer(g.chat_id)

async def main():
    store.games.clear(); store.user_to_chat.clear()
    total=0; seed=90_000
    plans=[('classic',700,4,18),('chaos',700,4,20),('virus',500,13,20),('clans',600,12,30)]
    for mode,n,lo,hi in plans:
        for i in range(n):
            count=lo+(i%(hi-lo+1))
            await one(seed,mode,count)
            seed+=1; total+=1
    print(f'ROLE INTERACTION FUZZ OK: {total} nights')

if __name__=='__main__': asyncio.run(main())
