"""管理员优先的显式好感度；自动分只补空白（见 affinity_watch.py）。"""
import json
import time


def _overview(store, limit=30):
    rows = store.db.execute(
        'SELECT a.sender_id,a.score,a.source,a.updated_at,p.name FROM birthday_affinity a'
        ' LEFT JOIN people p ON p.sender_id=a.sender_id'
        ' ORDER BY a.score DESC,a.updated_at DESC').fetchall()
    if not rows:
        return '好感度档案还是空的：等每日自动打分跑完再看，或先用 /记忆 好感度 设置 昵称 分数。'
    lines = ['好感度总览（共 %d 人，按分数从高到低）：' % len(rows)]
    for i, r in enumerate(rows[:limit], 1):
        name = r['name'] or str(r['sender_id'])
        how = '管理员' if str(r['source']) == 'admin_set' else '自动'
        day = time.strftime('%m-%d', time.localtime(float(r['updated_at'] or 0)))
        lines.append('%d. %s %d分（%s·%s）' % (i, name, int(r['score']), how, day))
    if len(rows) > limit:
        lines.append('……只显示前 %d 人。' % limit)
    lines.append('分数只用于生日祝福调整语气，不对群友公开。')
    return '\n'.join(lines)


def command(store, text, admin):
    if not admin:return '好感度档案仅限机器人管理员查看或设置。'
    action,_,rest=text.strip().partition(' ')
    if action=='总览':
        return _overview(store)
    if action not in ('查看','设置') or not rest.strip():
        return '用法：/记忆 好感度 查看 昵称；/记忆 好感度 总览；/记忆 好感度 设置 昵称 0—100。未设置的人由每日自动打分填充，管理员设置的分数优先。'
    who=rest.strip();score=None
    if action=='设置':
        try:who,raw=who.rsplit(' ',1);score=int(raw)
        except ValueError:return '设置格式：/记忆 好感度 设置 精确昵称 0—100'
        if not 0<=score<=100:return '好感度范围为0—100，未修改记录。'
    person=store.db.execute('SELECT * FROM people WHERE sender_id=?',(who,)).fetchone()
    if person is None:
        matches=[]
        for row in store.db.execute('SELECT * FROM people'):
            try:aliases=json.loads(row['aliases'] or '[]')
            except (ValueError,TypeError):aliases=[]
            if who==row['name'] or who in aliases:matches.append(row)
        if len(matches)>1:return '这个昵称对应多个人，请使用用户ID，未修改记录。'
        if not matches:return '请使用精确昵称或用户ID；不会用模糊匹配修改好感度。'
        person=matches[0]
    if action=='设置':store.set_affinity(person['sender_id'],score)
    row=store.get_affinity_row(person['sender_id'])
    if row is None:return person['name']+'：好感度未设置，将等下次自动打分（管理员也可手动设置）。'
    how='自动评分，管理员设置可覆盖' if str(row['source'])=='auto' else '管理员设定'
    return person['name']+f'：好感度 {row["score"]}/100（{how}）。生日祝福只据此调整语气，不公开分数。'
