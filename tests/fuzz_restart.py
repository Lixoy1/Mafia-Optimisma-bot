"""Restart/failure fuzz for Mafia Optimisma v3.

Randomly restarts the engine from its persisted snapshot during NIGHT,
DISCUSSION, NOMINATION, VERDICT and RESOLVING while driving real phase methods.
"""
from __future__ import annotations
import asyncio, random, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_core import FakeBot, FakeStorage, Settings, GameEngine, GameState, NightAction, Phase, PlayerState, generate_roles, store
from simulate_games import build_actions, choose

SETTINGS=Settings('x', registration_seconds=999, registration_warning_seconds=30, night_seconds=999, discussion_seconds=999, nomination_seconds=999, verdict_seconds=999)

async def restart(engine, storage, bot, chat_id):
    engine.cancel_timer(chat_id)
    w=engine.warning_tasks.pop(chat_id,None)
    if w and not w.done(): w.cancel()
    store.games.clear(); store.user_to_chat.clear()
    new=GameEngine(SETTINGS, storage)
    await new.restore_active_games(bot)
    g=store.get(chat_id)
    if g:
        new.cancel_timer(chat_id)
    return new,g

async def maybe_restart(rng, engine, storage, bot, game, probability=.42):
    if game and game.phase != Phase.FINISHED and rng.random()<probability:
        return await restart(engine, storage, bot, game.chat_id)
    return engine,game

async def one(seed:int):
    rng=random.Random(seed)
    random.seed(seed)
    store.games.clear(); store.user_to_chat.clear()
    storage=FakeStorage(); bot=FakeBot(); engine=GameEngine(SETTINGS,storage)
    mode=rng.choice(['classic','chaos','virus','clans'])
    low=12 if mode=='clans' else 4
    high=20 if mode!='clans' else 24
    n=rng.randint(low,high)
    chat=2_000_000+seed
    g=GameState(chat,f'fuzz-{seed}',mode=mode,phase=Phase.RESOLVING,started_at=time.time())
    roles=generate_roles(mode,n)
    for i,role in enumerate(roles,1):
        g.players[i]=PlayerState(i,f'P{i}',number=i,role_key=role,initial_role_key=role)
    store.games[chat]=g
    await engine.start_night(bot,g,allow_from_resolving=True); engine.cancel_timer(chat)

    for turn in range(n+30):
        if store.get(chat) is None: return turn+1
        engine,g=await maybe_restart(rng,engine,storage,bot,g)
        if g is None: return turn+1
        engine.cancel_timer(chat)

        if g.phase==Phase.RESOLVING:
            if g.temp.get('resume_action')=='check_win_then_start_night':
                winner=await engine.check_win(bot,g)
                if not winner: await engine.start_night(bot,g,allow_from_resolving=True)
                engine.cancel_timer(chat)
            else:
                await engine._resume_game(bot,g); engine.cancel_timer(chat)
            if store.get(chat) is None: return turn+1
        if g.phase!=Phase.NIGHT:
            raise AssertionError((seed,'expected night',g.phase,g.temp))

        # Sometimes save a partial night before a restart.
        all_actions=build_actions(g)
        if all_actions and rng.random()<.35:
            keys=list(all_actions); rng.shuffle(keys)
            partial=keys[:max(1,len(keys)//2)]
            g.actions={k:all_actions[k] for k in partial}
            await engine.persist(g)
            engine,g=await restart(engine,storage,bot,chat)
            if g is None: return turn+1
            engine.cancel_timer(chat)
            # Complete only actors that have not committed a move yet.
            fresh=build_actions(g)
            for uid,a in fresh.items():
                g.actions.setdefault(uid,a)
        else:
            g.actions=all_actions
        await engine.persist(g)
        await engine.end_night(bot,g); engine.cancel_timer(chat)
        if store.get(chat) is None: return turn+1

        engine,g=await maybe_restart(rng,engine,storage,bot,g)
        if g is None: return turn+1
        engine.cancel_timer(chat)
        if g.phase!=Phase.DISCUSSION:
            raise AssertionError((seed,'expected discussion',g.phase,g.temp))
        await engine.start_nomination(bot,g); engine.cancel_timer(chat)

        engine,g=await maybe_restart(rng,engine,storage,bot,g)
        if g is None: return turn+1
        engine.cancel_timer(chat)
        if g.phase!=Phase.NOMINATION:
            raise AssertionError((seed,'expected nomination',g.phase,g.temp))
        alive=g.alive_players()
        if not alive:
            await engine.check_win(bot,g); continue
        candidate=choose(alive)
        # random mix: no votes, tie-ish, or strong candidate
        style = 2 if turn % 4 == 3 else rng.randrange(4)
        if style==0:
            g.votes={}
        elif style==1 and len(alive)>=3:
            a,b=rng.sample(alive,2)
            voters=[p for p in alive if not p.silenced]
            g.votes={p.user_id:(a.user_id if i%2==0 else b.user_id) for i,p in enumerate(voters) if p.user_id not in {a.user_id,b.user_id}}
        else:
            g.votes={p.user_id:(None if p.user_id==candidate.user_id or p.silenced else candidate.user_id) for p in alive}
        await engine.persist(g)
        await engine.end_nomination(bot,g); engine.cancel_timer(chat)
        if store.get(chat) is None: return turn+1

        engine,g=await maybe_restart(rng,engine,storage,bot,g)
        if g is None: return turn+1
        engine.cancel_timer(chat)
        if g.phase==Phase.VERDICT:
            cand=g.get_player(g.nominated_id or 0)
            eligible=[p for p in g.alive_players() if cand and p.user_id!=cand.user_id and not p.silenced]
            # include no votes / pardon / execute
            vstyle = 2 if turn % 4 == 3 else rng.randrange(3)
            if vstyle==0: g.verdict_votes={}
            elif vstyle==1: g.verdict_votes={p.user_id:False for p in eligible}
            else: g.verdict_votes={p.user_id:True for p in eligible}
            await engine.persist(g)
            engine,g=await maybe_restart(rng,engine,storage,bot,g,probability=.55)
            if g is None: return turn+1
            engine.cancel_timer(chat)
            if g.phase!=Phase.VERDICT:
                # A restore of an expired/tiny phase is not expected with 999 sec.
                raise AssertionError((seed,'verdict changed unexpectedly',g.phase,g.temp))
            await engine.end_verdict(bot,g); engine.cancel_timer(chat)
            if store.get(chat) is None: return turn+1
        if g.bomb_pending_for and g.phase==Phase.NIGHT:
            # Randomly restart during the revenge NIGHT and then commit one target.
            engine,g=await maybe_restart(rng,engine,storage,bot,g,probability=.65)
            if g is None: return turn+1
            engine.cancel_timer(chat)
            if g.bomb_pending_for and not g.bomb_used and g.alive_players():
                g.temp['bomb_target_id']=choose(g.alive_players()).user_id
                g.bomb_used=True
                await engine.persist(g)
        if store.get(chat) is None: return turn+1
        if g.phase!=Phase.NIGHT:
            # end_nomination no-candidate path should already start next night.
            raise AssertionError((seed,'day did not advance',g.phase,g.temp))
    raise AssertionError((seed,'did not finish',mode,n,g.phase,[(p.user_id,p.role_key,p.alive) for p in g.players.values()]))

async def main():
    mx=0
    total=600
    for i in range(total):
        turns=await one(50_000+i)
        mx=max(mx,turns)
    print(f'RESTART FUZZ OK: {total} games, max_turns={mx}')

if __name__=='__main__': asyncio.run(main())
