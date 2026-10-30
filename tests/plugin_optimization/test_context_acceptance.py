"""Fixed synthetic corpus and negative controls for context acceptance."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('context_acceptance',ROOT/'scripts/diagnostics/context_acceptance.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


class RubricTests(unittest.TestCase):
    def answers(self):
        return {
            'Q1':'岚舟喜欢钢琴，不喜欢重金属；青禾喜欢重金属',
            'Q2':'岚舟现在无糖乌龙，原先柠檬茶已更正',
            'Q3':'青禾部署云团，不是小洛',
            'Q4':'星屿问天文摄影教程，小洛的回答不是星屿的经历',
            'Q5':'虚构调试文本，不是事实',
            'Q6':'暂定，未订票',
            'Q7':'岚舟 星海 original-A-731 第3项 task-original-19 下载中未发送',
            'Q8':'青禾 晚风 cover-B-842 第3项 task-cover-27 等待音源',
            'Q9':'云团，不能混用任务序号',
        }

    def grade(self,answers):return audit.grade_answers(json.dumps(answers,ensure_ascii=False))

    def test_reference_has_all_necessary_facts(self):
        self.assertEqual(self.grade(self.answers())['passed'],9)

    def test_lost_question_is_not_a_pass(self):
        answers=self.answers();answers['Q4']='unknown'
        self.assertFalse(self.grade(answers)['necessary_fact_checks']['Q4'])

    def test_cross_user_song_ids_are_detected(self):
        answers=self.answers();answers['Q7'],answers['Q8']=answers['Q8'],answers['Q7']
        checks=self.grade(answers)['necessary_fact_checks']
        self.assertFalse(checks['Q7']);self.assertFalse(checks['Q8'])

    def test_invented_delivery_fails(self):
        answers=self.answers();answers['Q7']='岚舟 星海 original-A-731 3 task-original-19 已下载并发送成功'
        self.assertFalse(self.grade(answers)['necessary_fact_checks']['Q7'])

    def test_unknown_never_counts_as_recalled(self):
        answers=self.answers();answers['Q3']='unknown 云团 青禾 小洛'
        self.assertFalse(self.grade(answers)['necessary_fact_checks']['Q3'])
