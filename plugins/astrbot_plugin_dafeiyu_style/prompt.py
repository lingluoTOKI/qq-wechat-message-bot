"""提示词常量：只描述任务与 JSON 结构，不拼人格、不带立场。"""

SYSTEM = (
    "你是群聊语料分析器，只做归纳，不扮演任何人、不发表观点、不编造事实。"
    "输入是群友的真实消息，输出必须是单个 JSON 对象，不能有解释文字、不能有 Markdown 代码块标记。"
)


def coarse_prompt(rows, cap: int = 40) -> str:
    lines = []
    for row in rows[:cap]:
        text = str(row.get("content", "")).replace("\n", " ")[:120]
        lines.append("%s. %s" % (row.get("id"), text))
    return (
        "下面是一个群里的消息列表（编号. 内容）。挑出真正体现「这个群怎么说人话」的消息，"
        "包括：稳定的措辞习惯、群内黑话/缩写、可复用的问答示例。普通打卡、链接、无信息量的消息不要。\n"
        "只输出 JSON：{\"keep\":[编号,...]}，最多 30 个。\n\n" + "\n".join(lines)
    )


def refine_prompt(rows, cap: int = 40) -> str:
    lines = []
    for row in rows[:cap]:
        text = str(row.get("content", "")).replace("\n", " ")[:120]
        lines.append("%s. %s" % (row.get("id"), text))
    return (
        "根据下列群聊消息，归纳可复用的表达规则、群内黑话、以及「用户这样说 → 机器人可以这样回」的示例。\n"
        "要求：\n"
        "1. 每条都必须附 evidence：直接从原文截取的片段（≤40 字），不得改写；\n"
        "2. rule 用一句话描述「怎么说」，例如「句子短、爱用省略主语、句尾常带语气词」；\n"
        "3. 黑话只收本群特有、外部看不懂的词；不确定就不要写；\n"
        "4. few_shots 只收能明确看出问与答关系的，bot_reply 必须是符合本群语气的自然回复；\n"
        "5. 宁缺毋滥，最多 6 条规则、5 条黑话、3 个示例。\n"
        "只输出 JSON：\n"
        "{\"rules\":[{\"rule\":\"...\",\"evidence\":\"...\"}],"
        "\"jargon\":[{\"term\":\"...\",\"meaning\":\"...\",\"confidence\":0.8}],"
        "\"few_shots\":[{\"user_text\":\"...\",\"bot_reply\":\"...\"}]}\n\n"
        + "\n".join(lines)
    )
