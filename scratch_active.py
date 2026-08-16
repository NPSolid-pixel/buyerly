import asyncio
from database.db import async_session_maker
from database.models import Account
from meta_api.client import MetaClient
from rules.engine import RuleEngine
from sqlalchemy import select

async def check_active_only():
    meta = MetaClient()
    async with async_session_maker() as session:
        for acc_id in ['act_1009347965254238', 'act_2033757857229842']:
            acc = (await session.execute(select(Account).where(Account.account_id == acc_id))).scalar_one_or_none()
            if not acc:
                continue
            print(f"\n==========================================")
            print(f"Кабинет: {acc.name} ({acc.account_id})")
            print(f"Автоправила: {'ВКЛЮЧЕНЫ' if acc.rules_enabled else 'ВЫКЛЮЧЕНЫ'}")
            print(f"Пресет: {acc.preset_name}")
            print(f"Действие: {acc.rule_action}")
            print(f"Условия: {acc.rule_conditions}")
            print(f"==========================================")
            try:
                adsets = await meta.get_adsets_insights(acc.account_id, acc.access_token, date_preset='today')
                active_adsets = [a for a in adsets if a.get('status') == 'ACTIVE' or float(a.get('spend', 0)) > 0]
                print(f"Активных адсетов с открутом: {len(active_adsets)}")
                for a in active_adsets:
                    res = RuleEngine.evaluate(a, acc)
                    spend = float(a.get('spend', 0.0))
                    leads = int(a.get('leads', 0))
                    cpl = (spend / leads) if leads > 0 else spend
                    print(f"  🎯 AdSet '{a.get('adset_name')}' ({a.get('adset_id')}):")
                    print(f"     Статус: {a.get('status')} | Спенд: ${spend:.2f} | Лидов: {leads} | CPL: ${cpl:.2f}")
                    print(f"     Действие правила: {res.action} ({res.reason})")
            except Exception as e:
                print(f"  Ошибка Meta API: {e}")

if __name__ == '__main__':
    asyncio.run(check_active_only())
