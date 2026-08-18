import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT))

# Reuse the offline aiogram/aiosqlite stubs and fakes from the core suite.
from test_core import FakeBot, FakeStorage

from mafia_optimisma.config import Settings
from mafia_optimisma.content import ROLES
from mafia_optimisma.engine import GameEngine, generate_roles, role_team
from mafia_optimisma.keyboards import night_action_keyboard
from mafia_optimisma.models import GameState, NightAction, Phase, PlayerState
from mafia_optimisma.protocol import decode_action
from mafia_optimisma.state import store


def buttons_data(markup):
    if not markup:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row if getattr(b, 'callback_data', None)]


def callback_action(data):
    if not data or not data.startswith("n:"):
        return None
    parts = data.split(":", 5)
    return decode_action(parts[4]) if len(parts) >= 6 else None


class RoleMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store.games.clear(); store.user_to_chat.clear()
        self.storage = FakeStorage()
        self.engine = GameEngine(Settings(
            'x', registration_seconds=100, registration_warning_seconds=30,
            night_seconds=100, discussion_seconds=100,
            nomination_seconds=100, verdict_seconds=100,
        ), self.storage)
        self.bot = FakeBot()

    async def asyncTearDown(self):
        for t in list(self.engine.tasks.values()) + list(self.engine.warning_tasks.values()):
            if not t.done(): t.cancel()

    def game(self, chat_id, mode='classic', day=1, *players):
        g = GameState(chat_id, 'matrix', mode=mode, phase=Phase.NIGHT, day=day)
        g.players = {p.user_id: p for p in players}
        store.games[g.chat_id] = g
        return g

    async def test_blocked_self_heal_does_not_spend_one_game_self_heal(self):
        diva = PlayerState(1,'Diva',role_key='night_diva')
        doc = PlayerState(2,'Doc',role_key='surgeon')
        don = PlayerState(3,'Don',role_key='carleone')
        g=self.game(6001,'classic',1,diva,doc,don)
        g.actions={
            1:NightAction(1,'block_and_silence',2,actor_role_key='night_diva'),
            2:NightAction(2,'heal',2,actor_role_key='surgeon'),
            3:NightAction(3,'mafia_kill',2,actor_role_key='carleone'),
        }
        await self.engine.resolve_night(self.bot,g)
        self.assertEqual(doc.self_heals_used,0,'a cancelled self-heal must remain available for a later night')
        self.assertFalse(doc.alive)

    async def test_heal_removes_diva_day_silence_but_does_not_restore_blocked_action(self):
        diva=PlayerState(1,'Diva',role_key='night_diva')
        target=PlayerState(2,'Cop',role_key='tracker')
        doc=PlayerState(3,'Doc',role_key='surgeon')
        suspect=PlayerState(4,'Suspect',role_key='carleone')
        g=self.game(6002,'classic',2,diva,target,doc,suspect)
        g.actions={
            1:NightAction(1,'block_and_silence',2,actor_role_key='night_diva'),
            2:NightAction(2,'check',4,actor_role_key='tracker'),
            3:NightAction(3,'heal',2,actor_role_key='surgeon'),
        }
        await self.engine.resolve_night(self.bot,g)
        self.assertTrue(target.blocked)
        self.assertFalse(target.silenced)
        texts=[m.text for m in self.bot.messages if m.chat_id==2]
        self.assertFalse(any('Проверка завершена' in t or 'Роль' in t and 'Suspect' in t for t in texts))

    async def test_don_choice_overrides_torpedo_choice(self):
        don=PlayerState(1,'Don',role_key='carleone')
        torp=PlayerState(2,'Mafia',role_key='torpedo')
        a=PlayerState(3,'A',role_key='optimist')
        b=PlayerState(4,'B',role_key='optimist')
        g=self.game(6003,'classic',1,don,torp,a,b)
        g.actions={1:NightAction(1,'mafia_kill',3,actor_role_key='carleone'),2:NightAction(2,'mafia_kill',4,actor_role_key='torpedo')}
        deaths,_=await self.engine.resolve_night(self.bot,g)
        self.assertFalse(a.alive); self.assertTrue(b.alive)
        self.assertEqual([p.user_id for p,_ in deaths],[3])

    async def test_torpedo_choice_is_used_when_don_does_not_act(self):
        don=PlayerState(1,'Don',role_key='carleone')
        torp=PlayerState(2,'Mafia',role_key='torpedo')
        a=PlayerState(3,'A',role_key='optimist')
        g=self.game(6004,'classic',1,don,torp,a)
        g.actions={2:NightAction(2,'mafia_kill',3,actor_role_key='torpedo')}
        await self.engine.resolve_night(self.bot,g)
        self.assertFalse(a.alive)

    async def test_morning_reports_death_before_don_promotion_and_summary(self):
        don=PlayerState(1,'Don',role_key='carleone')
        torp=PlayerState(2,'Mafia',role_key='torpedo')
        man=PlayerState(3,'Maniac',role_key='butcher')
        town1=PlayerState(4,'Town1',role_key='optimist')
        town2=PlayerState(5,'Town2',role_key='optimist')
        g=self.game(6050,'classic',1,don,torp,man,town1,town2)
        g.actions={3:NightAction(3,'solo_kill',1,actor_role_key='butcher')}
        await self.engine.end_night(self.bot,g)
        group=[m.text for m in self.bot.messages if m.chat_id==g.chat_id]
        day_i=next(i for i,t in enumerate(group) if 'День 1' in t)
        death_i=next(i for i,t in enumerate(group) if 'ночной кошмар' in t and 'Don' in t)
        promotion_i=next(i for i,t in enumerate(group) if 'Мафия стала' in t and 'Карлеоне' in t)
        summary_i=next(i for i,t in enumerate(group) if 'Живые игроки' in t)
        self.assertLess(day_i, death_i)
        self.assertLess(death_i, promotion_i)
        self.assertLess(promotion_i, summary_i)
        self.assertEqual(torp.role_key,'carleone')
        self.engine.cancel_timer(g.chat_id)

    async def test_dead_don_promotes_torpedo_and_notifies(self):
        don=PlayerState(1,'Don',role_key='carleone',alive=False)
        torp=PlayerState(2,'Mafia',role_key='torpedo')
        town=PlayerState(3,'Town',role_key='optimist')
        g=self.game(6005,'classic',1,don,torp,town)
        promotions=self.engine._inherit_roles(g)
        await self.engine._announce_promotions(self.bot,g,promotions)
        self.assertEqual(torp.role_key,'carleone')
        self.assertTrue(any(m.chat_id==2 and 'новая роль' in m.text.lower() for m in self.bot.messages))
        self.assertTrue(any(m.chat_id==g.chat_id and 'Карлеоне' in m.text for m in self.bot.messages))

    async def test_bodyguard_dies_instead_of_target_even_if_doctor_heals_guard(self):
        guard=PlayerState(1,'Guard',role_key='shield')
        victim=PlayerState(2,'Victim',role_key='optimist')
        doc=PlayerState(3,'Doc',role_key='surgeon')
        don=PlayerState(4,'Don',role_key='carleone')
        g=self.game(6006,'classic',1,guard,victim,doc,don)
        g.actions={
            1:NightAction(1,'bodyguard',2,actor_role_key='shield'),
            3:NightAction(3,'heal',1,actor_role_key='surgeon'),
            4:NightAction(4,'mafia_kill',2,actor_role_key='carleone'),
        }
        await self.engine.resolve_night(self.bot,g)
        self.assertFalse(guard.alive)
        self.assertTrue(victim.alive)

    async def test_bodyguard_only_absorbs_one_of_two_independent_attacks(self):
        guard=PlayerState(1,'Guard',role_key='shield')
        victim=PlayerState(2,'Victim',role_key='optimist')
        don=PlayerState(3,'Don',role_key='carleone')
        man=PlayerState(4,'Maniac',role_key='butcher')
        g=self.game(6007,'chaos',1,guard,victim,don,man)
        g.actions={
            1:NightAction(1,'bodyguard',2,actor_role_key='shield'),
            3:NightAction(3,'mafia_kill',2,actor_role_key='carleone'),
            4:NightAction(4,'solo_kill',2,actor_role_key='butcher'),
        }
        deaths,events=await self.engine.resolve_night(self.bot,g)
        self.assertFalse(guard.alive)
        self.assertFalse(victim.alive)
        self.assertCountEqual([p.user_id for p,_ in deaths],[1,2])
        self.assertGreaterEqual(len(events),2)

    async def test_lucky_75_percent_boundary(self):
        don=PlayerState(1,'Don',role_key='carleone')
        lucky=PlayerState(2,'Lucky',role_key='lucky')
        g=self.game(6008,'classic',1,don,lucky)
        g.actions={1:NightAction(1,'mafia_kill',2,actor_role_key='carleone')}
        with patch('mafia_optimisma.engine.random.randint',return_value=75):
            await self.engine.resolve_night(self.bot,g)
        self.assertTrue(lucky.alive)

        lucky.alive=True
        g.actions={1:NightAction(1,'mafia_kill',2,actor_role_key='carleone')}
        with patch('mafia_optimisma.engine.random.randint',return_value=76):
            await self.engine.resolve_night(self.bot,g)
        self.assertFalse(lucky.alive)

    async def test_mafia_alibi_fools_commissioner(self):
        alibi=PlayerState(1,'Alibi',role_key='alibi_master')
        mafia=PlayerState(2,'Mafia',role_key='torpedo')
        cop=PlayerState(3,'Cop',role_key='tracker')
        g=self.game(6009,'classic',2,alibi,mafia,cop)
        g.actions={
            1:NightAction(1,'mafia_mask',2,actor_role_key='alibi_master'),
            3:NightAction(3,'check',2,actor_role_key='tracker'),
        }
        await self.engine.resolve_night(self.bot,g)
        cop_text='\n'.join(m.text for m in self.bot.messages if m.chat_id==3)
        self.assertIn(ROLES['optimist'].name,cop_text)
        self.assertNotIn(ROLES['torpedo'].name,cop_text)

    async def test_blocked_alibi_does_not_fool_commissioner(self):
        diva=PlayerState(1,'Diva',role_key='night_diva')
        alibi=PlayerState(2,'Alibi',role_key='alibi_master')
        mafia=PlayerState(3,'Mafia',role_key='torpedo')
        cop=PlayerState(4,'Cop',role_key='tracker')
        g=self.game(6010,'classic',2,diva,alibi,mafia,cop)
        g.actions={
            1:NightAction(1,'block_and_silence',2,actor_role_key='night_diva'),
            2:NightAction(2,'mafia_mask',3,actor_role_key='alibi_master'),
            4:NightAction(4,'check',3,actor_role_key='tracker'),
        }
        await self.engine.resolve_night(self.bot,g)
        cop_text='\n'.join(m.text for m in self.bot.messages if m.chat_id==4)
        self.assertIn(ROLES['torpedo'].name,cop_text)

    async def test_forger_fools_hacker_about_yakuza_only(self):
        forger=PlayerState(1,'Forger',role_key='forger')
        yakuza=PlayerState(2,'Yakuza',role_key='samurai')
        hacker=PlayerState(3,'Hacker',role_key='breacher')
        cop=PlayerState(4,'Cop',role_key='tracker')
        g=self.game(6011,'clans',2,forger,yakuza,hacker,cop)
        g.actions={
            1:NightAction(1,'yakuza_mask',2,actor_role_key='forger'),
            3:NightAction(3,'mafia_role_check',2,actor_role_key='breacher'),
            4:NightAction(4,'check',2,actor_role_key='tracker'),
        }
        await self.engine.resolve_night(self.bot,g)
        hacker_text='\n'.join(m.text for m in self.bot.messages if m.chat_id==3)
        cop_text='\n'.join(m.text for m in self.bot.messages if m.chat_id==4)
        self.assertIn(ROLES['optimist'].name,hacker_text)
        self.assertIn(ROLES['samurai'].name,cop_text)

    async def test_helpers_see_doctor_and_commissioner_targets(self):
        doc=PlayerState(1,'Doc',role_key='surgeon')
        sis=PlayerState(2,'Sis',role_key='mercy_sister')
        cop=PlayerState(3,'Cop',role_key='tracker')
        cadet=PlayerState(4,'Cadet',role_key='cadet')
        target=PlayerState(5,'Target',role_key='optimist')
        g=self.game(6012,'classic',2,doc,sis,cop,cadet,target)
        g.actions={
            1:NightAction(1,'heal',5,actor_role_key='surgeon'),
            3:NightAction(3,'check',5,actor_role_key='tracker'),
        }
        await self.engine.resolve_night(self.bot,g)
        self.assertTrue(any(m.chat_id==2 and 'Target' in m.text for m in self.bot.messages))
        self.assertTrue(any(m.chat_id==4 and 'Target' in m.text for m in self.bot.messages))

    async def test_reporter_compares_clans(self):
        rep=PlayerState(1,'Rep',role_key='reporter')
        a=PlayerState(2,'A',role_key='surgeon')
        b=PlayerState(3,'B',role_key='tracker')
        c=PlayerState(4,'C',role_key='carleone')
        g=self.game(6013,'classic',2,rep,a,b,c)
        g.actions={1:NightAction(1,'compare_clans',2,3,actor_role_key='reporter')}
        await self.engine.resolve_night(self.bot,g)
        same='\n'.join(m.text for m in self.bot.messages if m.chat_id==1)
        self.assertIn('один клан',same)
        self.bot.messages.clear()
        g.actions={1:NightAction(1,'compare_clans',2,4,actor_role_key='reporter')}
        await self.engine.resolve_night(self.bot,g)
        diff='\n'.join(m.text for m in self.bot.messages if m.chat_id==1)
        self.assertIn('разные кланы',diff)

    async def test_werewolf_doctor_visit_turns_into_mercy_sister(self):
        wolf=PlayerState(1,'Wolf',role_key='werewolf')
        doc=PlayerState(2,'Doc',role_key='surgeon')
        g=self.game(6014,'classic',2,wolf,doc)
        g.actions={2:NightAction(2,'heal',1,actor_role_key='surgeon')}
        await self.engine.resolve_night(self.bot,g)
        self.assertEqual(wolf.role_key,'mercy_sister')

    async def test_werewolf_hacker_visit_turns_into_mafia(self):
        wolf=PlayerState(1,'Wolf',role_key='werewolf')
        hacker=PlayerState(2,'Hacker',role_key='breacher')
        g=self.game(6015,'classic',2,wolf,hacker)
        g.actions={2:NightAction(2,'mafia_role_check',1,actor_role_key='breacher')}
        await self.engine.resolve_night(self.bot,g)
        self.assertEqual(role_team(wolf.role_key),'mafia')

    async def test_carrier_doctor_75_cures_carrier(self):
        carrier=PlayerState(1,'Carrier',role_key='carrier')
        doc=PlayerState(2,'Doc',role_key='surgeon')
        g=self.game(6016,'virus',2,carrier,doc)
        g.actions={2:NightAction(2,'heal',1,actor_role_key='surgeon')}
        with patch('mafia_optimisma.engine.random.randint',return_value=75):
            await self.engine.resolve_night(self.bot,g)
        self.assertEqual(carrier.role_key,'optimist')
        self.assertEqual(doc.role_key,'surgeon')

    async def test_carrier_doctor_25_failure_infects_doctor(self):
        carrier=PlayerState(1,'Carrier',role_key='carrier')
        doc=PlayerState(2,'Doc',role_key='surgeon')
        g=self.game(6017,'virus',2,carrier,doc)
        g.actions={2:NightAction(2,'heal',1,actor_role_key='surgeon')}
        with patch('mafia_optimisma.engine.random.randint',return_value=100):
            await self.engine.resolve_night(self.bot,g)
        self.assertEqual(carrier.role_key,'carrier')
        self.assertEqual(doc.role_key,'carrier')

    async def test_all_living_infected_is_infected_win(self):
        a=PlayerState(1,'A',role_key='carrier',infected_spread_count=1)
        b=PlayerState(2,'B',role_key='carrier')
        g=self.game(6018,'virus',2,a,b)
        result=await self.engine.check_win(self.bot,g)
        self.assertEqual(result,'infected')
        self.assertIsNone(store.get(g.chat_id))

    async def test_joker_swaps_roles_and_each_target_only_once(self):
        joker=PlayerState(1,'Joker',role_key='joker')
        a=PlayerState(2,'A',role_key='surgeon')
        b=PlayerState(3,'B',role_key='tracker')
        c=PlayerState(4,'C',role_key='optimist')
        g=self.game(6019,'chaos',2,joker,a,b,c)
        g.actions={1:NightAction(1,'swap_roles',2,3,actor_role_key='joker')}
        await self.engine.resolve_night(self.bot,g)
        self.assertEqual(a.role_key,'tracker'); self.assertEqual(b.role_key,'surgeon')
        self.assertTrue(a.swapped_once and b.swapped_once)
        g.actions={1:NightAction(1,'swap_roles',2,4,actor_role_key='joker')}
        await self.engine.resolve_night(self.bot,g)
        self.assertEqual(a.role_key,'tracker'); self.assertEqual(c.role_key,'optimist')

    async def test_classic_commissioner_shoots_from_night_two_only(self):
        cop=PlayerState(1,'Cop',role_key='tracker')
        target=PlayerState(2,'T',role_key='optimist')
        g=self.game(6020,'classic',1,cop,target)
        data=buttons_data(night_action_keyboard(g,cop))
        self.assertFalse(any(callback_action(x) == 'shoot' for x in data))
        g.day=2
        data=buttons_data(night_action_keyboard(g,cop))
        self.assertTrue(any(callback_action(x) == 'shoot' for x in data))

    async def test_chaos_role_pack_keeps_mafia_at_one_per_three_including_specialists(self):
        # Newer Black Mafia ANARCHY help: mafia faction is 1 per 3 players;
        # Hacker/Spy/Lawyer are mafia specialists and therefore replace generic
        # Mafia slots rather than expanding the faction beyond that ratio.
        for count in range(3,22):
            roles=generate_roles('chaos',count)
            self.assertEqual(len(roles),count)
            mafia=sum(1 for role in roles if role_team(role)=='mafia')
            self.assertEqual(mafia,max(1,count//3),f'wrong mafia quota at {count} players: {roles}')
            self.assertNotIn('optimist',roles,f'ANARCHY should have no civilians through 21 players: {count}')

    async def test_chaos_nine_players_uses_hacker_inside_three_mafia_slots(self):
        roles=generate_roles('chaos',9)
        self.assertCountEqual(
            roles,
            ['carleone','torpedo','breacher','surgeon','tracker','butcher','joker','bomber','night_diva']
        )

    async def test_chaos_twenty_players_contains_all_documented_threshold_roles(self):
        roles=generate_roles('chaos',20)
        for expected in [
            'surgeon','tracker','butcher','joker','bomber','night_diva','breacher',
            'wanderer','lucky','shadow','fatalist','cadet','alibi_master','werewolf',
            'shield','mercy_sister','reporter'
        ]:
            self.assertIn(expected,roles)
        self.assertEqual(sum(1 for r in roles if role_team(r)=='mafia'),6)
        self.assertNotIn('optimist',roles)

    async def test_chaos_friendly_fire_allows_mafia_target(self):
        don=PlayerState(1,'Don',role_key='carleone')
        mate=PlayerState(2,'Mate',role_key='torpedo')
        town=PlayerState(3,'Town',role_key='optimist')
        g=self.game(6021,'chaos',1,don,mate,town)
        data=buttons_data(night_action_keyboard(g,don))
        self.assertTrue(any(x.endswith(':2') for x in data if callback_action(x) == 'mafia_kill'))
        g.mode='classic'
        data=buttons_data(night_action_keyboard(g,don))
        self.assertFalse(any(x.endswith(':2') for x in data if callback_action(x) == 'mafia_kill'))

    async def test_team_chat_reaches_only_same_crime_team(self):
        don=PlayerState(1,'Don',role_key='carleone')
        mate=PlayerState(2,'Mate',role_key='torpedo')
        yakuza=PlayerState(3,'Samurai',role_key='samurai')
        town=PlayerState(4,'Town',role_key='optimist')
        g=self.game(6022,'clans',1,don,mate,yakuza,town)
        sent=await self.engine.team_chat(self.bot,g,don,'цель — Town')
        self.assertTrue(sent)
        recipients=[m.chat_id for m in self.bot.messages]
        self.assertIn(2,recipients); self.assertNotIn(3,recipients); self.assertNotIn(4,recipients)

    async def test_dead_faction_members_do_not_get_ordinary_team_win(self):
        alive_town=PlayerState(1,'AliveTown',role_key='tracker',alive=True)
        dead_town=PlayerState(2,'DeadTown',role_key='surgeon',alive=False)
        dead_mafia=PlayerState(3,'DeadMafia',role_key='carleone',alive=False)
        g=self.game(6023,'classic',2,alive_town,dead_town,dead_mafia)
        g.started_at=1
        await self.engine.finish_game(self.bot,g,'town')
        rewarded_winners={uid for uid,win,*_ in self.storage.rewards if win}
        self.assertEqual(rewarded_winners,{1})

    async def test_dead_bodyguard_wins_if_saved_player_wins(self):
        guard=PlayerState(1,'Guard',role_key='shield',alive=False,bodyguard_saved_id=2)
        saved=PlayerState(2,'Saved',role_key='tracker',alive=True)
        dead_other=PlayerState(3,'DeadOther',role_key='surgeon',alive=False)
        g=self.game(6024,'classic',2,guard,saved,dead_other)
        g.started_at=1
        await self.engine.finish_game(self.bot,g,'town')
        rewarded_winners={uid for uid,win,*_ in self.storage.rewards if win}
        self.assertEqual(rewarded_winners,{1,2})

    async def test_dead_bodyguard_does_not_win_if_saved_player_loses(self):
        guard=PlayerState(1,'Guard',role_key='shield',alive=False,bodyguard_saved_id=2)
        saved_loser=PlayerState(2,'SavedMafia',role_key='carleone',alive=False)
        town=PlayerState(3,'Town',role_key='tracker',alive=True)
        g=self.game(6025,'classic',2,guard,saved_loser,town)
        g.started_at=1
        await self.engine.finish_game(self.bot,g,'town')
        rewarded_winners={uid for uid,win,*_ in self.storage.rewards if win}
        self.assertEqual(rewarded_winners,{3})

    async def test_doctor_and_mercy_sister_have_private_night_channel(self):
        doc=PlayerState(1,'Doc',role_key='surgeon')
        sis=PlayerState(2,'Sis',role_key='mercy_sister')
        town=PlayerState(3,'Town',role_key='optimist')
        g=self.game(6026,'classic',2,doc,sis,town)
        sent=await self.engine.team_chat(self.bot,g,doc,'лечу третьего')
        self.assertTrue(sent)
        recipients=[m.chat_id for m in self.bot.messages]
        self.assertIn(2,recipients); self.assertNotIn(3,recipients)
        self.assertTrue(any(m.chat_id==2 and 'Напарник Doc' in m.text for m in self.bot.messages))

    async def test_lynched_bomber_revenge_is_resolved_during_next_night(self):
        bomber=PlayerState(1,'Bomber',role_key='bomber',alive=False)
        target=PlayerState(2,'Target',role_key='optimist',alive=True)
        don=PlayerState(3,'Don',role_key='carleone',alive=True)
        g=GameState(6027,'bomb',mode='classic',phase=Phase.RESOLVING,day=1)
        g.players={1:bomber,2:target,3:don}
        g.bomb_pending_for=1
        g.bomb_used=False
        store.games[g.chat_id]=g
        await self.engine.start_night(self.bot,g,allow_from_resolving=True)
        self.assertEqual(g.phase,Phase.NIGHT)
        self.assertIn(1,g.night_pm_message_ids,'dead bomber must receive revenge controls during NIGHT')
        # Simulate the accepted callback payload; the callback itself stores this.
        g.temp['bomb_target_id']=2
        g.bomb_used=True
        deaths,events=await self.engine.resolve_night(self.bot,g)
        self.assertFalse(target.alive)
        self.assertIn(2,[p.user_id for p,_ in deaths])
        self.assertTrue(any('Подрывник забрал' in e for e in events))
        self.assertIsNone(g.bomb_pending_for)

    async def test_bomber_revenge_expires_if_no_target_chosen(self):
        bomber=PlayerState(1,'Bomber',role_key='bomber',alive=False)
        target=PlayerState(2,'Target',role_key='optimist',alive=True)
        g=self.game(6028,'classic',2,bomber,target)
        g.bomb_pending_for=1
        deaths,events=await self.engine.resolve_night(self.bot,g)
        self.assertTrue(target.alive)
        self.assertEqual(deaths,[])
        self.assertIsNone(g.bomb_pending_for)

    async def test_bonebreaker_blocks_diva_even_when_they_target_each_other(self):
        diva=PlayerState(1,'Diva',role_key='night_diva')
        bone=PlayerState(2,'Bone',role_key='bonebreaker')
        town=PlayerState(3,'Town',role_key='optimist')
        g=self.game(6029,'clans',2,diva,bone,town)
        # Insert Diva first on purpose; outcome must not depend on callback order.
        g.actions={
            1:NightAction(1,'block_and_silence',2,actor_role_key='night_diva'),
            2:NightAction(2,'block_and_silence',1,actor_role_key='bonebreaker'),
        }
        await self.engine.resolve_night(self.bot,g)
        self.assertTrue(diva.blocked)
        self.assertFalse(bone.blocked)
        self.assertTrue(diva.silenced)

    async def test_same_night_resolver_retry_keeps_self_heal_result_idempotent(self):
        doc=PlayerState(1,'Doc',role_key='surgeon')
        don=PlayerState(2,'Don',role_key='carleone')
        g=self.game(6030,'classic',1,doc,don)
        g.actions={
            1:NightAction(1,'heal',1,actor_role_key='surgeon'),
            2:NightAction(2,'mafia_kill',1,actor_role_key='carleone'),
        }
        deaths1,_=await self.engine.resolve_night(self.bot,g)
        self.assertTrue(doc.alive); self.assertEqual(doc.self_heals_used,1); self.assertEqual(deaths1,[])
        # Simulate retry of the exact same persisted NIGHT.
        deaths2,_=await self.engine.resolve_night(self.bot,g)
        self.assertTrue(doc.alive); self.assertEqual(doc.self_heals_used,1); self.assertEqual(deaths2,[])

    async def test_joker_keyboard_hides_players_whose_role_was_already_changed(self):
        joker=PlayerState(1,'Joker',role_key='joker')
        used=PlayerState(2,'Used',role_key='surgeon',swapped_once=True)
        fresh=PlayerState(3,'Fresh',role_key='tracker')
        g=self.game(6031,'chaos',2,joker,used,fresh)
        data=buttons_data(night_action_keyboard(g,joker))
        swap_targets=[x for x in data if callback_action(x) == 'swap1']
        self.assertFalse(any(x.endswith(':2') for x in swap_targets))
        self.assertTrue(any(x.endswith(':3') for x in swap_targets))

    async def test_bomber_revenge_happens_before_parity_win_is_declared(self):
        don=PlayerState(1,'Don',number=1,role_key='carleone',alive=True)
        bomber=PlayerState(2,'Bomber',number=2,role_key='bomber',alive=True)
        town=PlayerState(3,'Town',number=3,role_key='optimist',alive=True)
        g=GameState(6032,'bomb-verdict',mode='classic',phase=Phase.VERDICT,day=1,started_at=1)
        g.players={1:don,2:bomber,3:town}
        g.nominated_id=2
        g.verdict_votes={1:True,3:True}
        store.games[g.chat_id]=g
        await self.engine.end_verdict(self.bot,g)
        self.engine.cancel_timer(g.chat_id)
        self.assertIs(store.get(g.chat_id),g,'mafia parity must wait for bomber revenge night')
        self.assertEqual(g.phase,Phase.NIGHT)
        self.assertFalse(bomber.alive)
        self.assertEqual(g.bomb_pending_for,2)
        g.temp['bomb_target_id']=1
        g.bomb_used=True
        await self.engine.end_night(self.bot,g)
        self.engine.cancel_timer(g.chat_id)
        self.assertFalse(don.alive)
        self.assertIsNone(store.get(g.chat_id),'town should win after bomber removes the last mafia')
        winners={uid for uid,win,*_ in self.storage.rewards if win}
        self.assertEqual(winners,{3})

    async def test_restart_during_bomber_revenge_night_restores_dead_bomber_controls(self):
        bomber=PlayerState(1,'Bomber',number=1,role_key='bomber',alive=False)
        a=PlayerState(2,'A',number=2,role_key='optimist')
        don=PlayerState(3,'Don',number=3,role_key='carleone')
        g=GameState(6033,'bomb-restart',mode='classic',phase=Phase.NIGHT,day=2)
        g.players={1:bomber,2:a,3:don}
        g.bomb_pending_for=1
        g.phase_version=4
        import time as _time
        g.phase_deadline=_time.time()+60
        await self.storage.save_game_state(g)
        store.games.clear(); store.user_to_chat.clear()
        restored_engine=GameEngine(self.engine.settings,self.storage)
        await restored_engine.restore_active_games(self.bot)
        restored=store.get(g.chat_id)
        self.assertIsNotNone(restored)
        self.assertIn(1,restored.night_pm_message_ids)
        restored_engine.cancel_timer(g.chat_id)

    async def test_all_surviving_carriers_win_even_without_personal_spread(self):
        a=PlayerState(1,'A',role_key='carrier',infected_spread_count=2)
        b=PlayerState(2,'B',role_key='carrier',infected_spread_count=0)
        c=PlayerState(3,'C',role_key='carrier',infected_spread_count=0)
        g=self.game(6030,'virus',2,a,b,c)
        g.started_at=1
        await self.engine.check_win(self.bot,g)
        rewarded_winners={uid for uid,win,*_ in self.storage.rewards if win}
        self.assertEqual(rewarded_winners,{1,2,3})

    async def test_role_generation_never_starts_at_immediate_faction_win(self):
        # Generated packs must require gameplay before an ordinary faction can win.
        for mode, minimum in [('classic',4),('chaos',3),('virus',13),('clans',12)]:
            for n in range(minimum,31):
                roles=generate_roles(mode,n)
                players=[PlayerState(i+1,f'P{i+1}',role_key=r) for i,r in enumerate(roles)]
                g=self.game(6100+n+(0 if mode=='classic' else 100 if mode=='chaos' else 200 if mode=='virus' else 300),mode,1,*players)
                teams={team:sum(1 for p in players if role_team(p.role_key)==team) for team in {'town','mafia','yakuza','maniac','infected'}}
                if mode=='clans':
                    self.assertFalse(teams['mafia'] == 0 and teams['yakuza'] == 0 and teams['maniac'] == 0)
                    self.assertFalse(teams['mafia'] > 0 and teams['yakuza'] == 0 and teams['mafia'] >= n-teams['mafia'])
                    self.assertFalse(teams['yakuza'] > 0 and teams['mafia'] == 0 and teams['yakuza'] >= n-teams['yakuza'])
                elif mode=='virus' and teams['infected'] == n:
                    self.fail(f'virus pack starts already won at n={n}')
                else:
                    self.assertFalse(teams['mafia'] == 0 and teams['maniac'] == 0)
                    self.assertFalse(teams['mafia'] > 0 and teams['mafia'] >= n-teams['mafia'])


if __name__=='__main__':
    unittest.main(verbosity=2)
