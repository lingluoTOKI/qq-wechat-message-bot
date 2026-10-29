import importlib
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock

if 'wushu_review' not in sys.modules:
    package = types.ModuleType('wushu_review')
    package.__path__ = [os.path.dirname(os.path.dirname(__file__))]
    sys.modules['wushu_review'] = package
rankings = importlib.import_module('wushu_review.rankings')


class RankingsTests(unittest.IsolatedAsyncioTestCase):
    def api(self, **changes):
        data = dict(rankingScope='cumulative', day='2026-10-29', rows=[
            dict(rank=1, name='同名', points=8), dict(rank=1, name='同名', points=8),
            dict(rank=3, name='其他', points=2)])
        data.update(changes)
        return types.SimpleNamespace(daily_challenges=AsyncMock(return_value=data))

    async def test_ties_and_duplicate_names_preserved(self):
        api = self.api()
        result = await rankings.ranking_text(api, '单人', '同名')
        self.assertEqual(result.count('第1名 · 同名 · 8分'), 2)
        self.assertNotIn('其他', result)
        api.daily_challenges.assert_awaited_once_with('solo', ranking=True, timeout=8)

    async def test_both_divisions(self):
        api = self.api()
        result = await rankings.ranking_text(api)
        self.assertIn('单人累计积分榜', result)
        self.assertIn('多人累计积分榜', result)
        self.assertEqual(api.daily_challenges.await_count, 2)

    async def test_scope_and_malformed_rows_rejected(self):
        for changes in [dict(rankingScope='daily'), dict(rows=None), dict(rows=[{}])]:
            result = await rankings.ranking_text(self.api(**changes), 'solo')
            self.assertIn('数据格式异常', result)

    async def test_empty_and_missing_player(self):
        self.assertIn('暂无上榜成绩', await rankings.ranking_text(self.api(rows=[]), 'solo'))
        self.assertIn('不代表没有成绩', await rankings.ranking_text(self.api(), 'solo', '不存在'))

    async def test_failure_does_not_hide_other_board(self):
        api = self.api()
        data = api.daily_challenges.return_value
        api.daily_challenges.side_effect = [rankings.NetError('offline'), data]
        result = await rankings.ranking_text(api)
        self.assertIn('单人榜查询失败', result)
        self.assertIn('多人累计积分榜', result)

    async def test_invalid_input_never_requests(self):
        api = self.api()
        await rankings.ranking_text(api, 'unknown')
        await rankings.ranking_text(api, limit=21)
        api.daily_challenges.assert_not_awaited()
