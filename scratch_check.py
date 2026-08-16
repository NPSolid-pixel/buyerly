import asyncio
from database.db import async_session_maker
from database.models import Account
from meta_api.client import MetaClient
from rules.engine import RuleEngine
from sqlalchemy import select

async def check_live():
    meta = MetaClient()
    async with async_session_maker() as session:
        for acc_id in ['act_1009347965254238', 'act_2033757857229842']:
            acc = (await session.execute(select(Account).where(Account.account_id == acc_id))).scalar_one_or_none()
            if not acc:
                print(f"Account {acc_id} not found")
                continue
            print(f"\n=== Checking {acc.name} ({acc.account_id}) ===")
            print(f"Rules enabled: {acc.rules_enabled} | Preset: {acc.preset_name} | Action: {acc.rule_action} | Conditions: {acc.rule_conditions}")
            try:
                adsets = await meta.get_adsets_insights(acc.account_id, acc.access_token, date_preset='today')
                print(f"Found {len(adsets)} adsets today:")
                for a in adsets:
                    res = RuleEngine.evaluate(a, acc)
                    spend = float(a.get('spend', 0.0))
                    leads = int(a.get('leads', 0))
                    cpl = (spend / leads) if leads > 0 else spend
                    print(f"  - AdSet: {a.get('adset_name')} ({a.get('adset_id')}) | Status: {a.get('status')} | Spend: ${spend:.2f} | Leads: {leads} | CPL: ${cpl:.2f} | Action: {res.action} | Reason: {res.reason}")
            except Exception as e:
                print(f"  Error: {e}")

if __name__ == '__main__':
    asyncio.run(check_live())
