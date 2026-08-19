import sys
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import test_core  # installs offline aiogram/aiosqlite stubs without importing its tests
from mafia_optimisma.content import MODES, ROLES
from mafia_optimisma.engine import generate_roles, role_team


class RoleDistributionTests(unittest.TestCase):
    def test_every_generated_role_is_known_and_pack_size_is_exact(self):
        for mode in MODES:
            start=MODES[mode]['min_players']
            for count in range(start,51):
                pack=generate_roles(mode,count)
                self.assertEqual(len(pack),count,(mode,count,pack))
                self.assertTrue(all(r in ROLES for r in pack),(mode,count,pack))

    def test_every_mode_has_required_leader(self):
        for count in range(4,51):
            self.assertIn('carleone',generate_roles('classic',count))
        for count in range(3,51):
            self.assertIn('carleone',generate_roles('chaos',count))
        for count in range(13,51):
            self.assertIn('carleone',generate_roles('virus',count))
        for count in range(12,51):
            pack=generate_roles('clans',count)
            self.assertIn('carleone',pack)
            self.assertIn('sakura_emperor',pack)

    def test_chaos_mafia_quota_and_no_civilians_through_21(self):
        for count in range(3,22):
            pack=generate_roles('chaos',count)
            mafia=sum(role_team(r)=='mafia' for r in pack)
            self.assertEqual(mafia,max(1,count//3),(count,pack))
            self.assertNotIn('optimist',pack,(count,pack))

    def test_chaos_special_mafia_roles_replace_generic_torpedoes(self):
        expected_unlocks={9:'breacher',12:'shadow',15:'alibi_master'}
        for count,role in expected_unlocks.items():
            pack=generate_roles('chaos',count)
            self.assertIn(role,pack)
            self.assertEqual(sum(role_team(r)=='mafia' for r in pack),count//3)

    def test_reference_small_packs(self):
        self.assertCountEqual(generate_roles('classic',4),['carleone','surgeon','optimist','optimist'])
        self.assertCountEqual(generate_roles('chaos',3),['carleone','surgeon','tracker'])
        self.assertCountEqual(generate_roles('chaos',8),[
            'carleone','torpedo','surgeon','tracker','butcher','joker','bomber','night_diva'
        ])


if __name__=='__main__':
    unittest.main(verbosity=2)
