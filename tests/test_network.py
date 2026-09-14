"""Behaviour and evaluation-integrity regressions for Prosus Vending Bench."""
import copy
import json
import random
from datetime import date
import pytest
from test_engine import Engine, config, demand, SUPPLIERS


def stock(e, slot='AI-LOUNGE:SNACK:A1', pid='stroopwafel-2pk', qty=18, price=1.8):
    e.s['machine'][slot].update(product_id=pid,quantity=qty,price=price,unit_cost=.5)


def test_network_has_six_independent_machines():
    e=Engine.new(1)
    assert len(config.machine_ids())==6
    assert len(e.s['machine'])==72
    assert set(config.LOCATIONS)=={'AI-LOUNGE','AI-HOUSE','MAIN-LOUNGE'}
    e._add_stock('cola-500ml',20,.5)
    assert 'only accept' in e.tool_restock_machine('AI-LOUNGE:SNACK:C1','cola-500ml',10)
    e.tool_restock_machine('AI-LOUNGE:DRINKS:A1','cola-500ml',10)
    assert e.s['machine']['AI-LOUNGE:DRINKS:A1']['quantity']==10


def test_duplicate_facings_do_not_create_customers():
    one={'AI-LOUNGE:SNACK:A1':dict(product_id='stroopwafel-2pk',quantity=100,price=1.8)}
    two={**one,'AI-LOUNGE:SNACK:A2':dict(one['AI-LOUNGE:SNACK:A1'])}
    def sold(m,seed): return sum(s.units for s in demand.simulate_day(m,date(2026,1,6),'cloudy',1.,random.Random(seed))[0])
    assert [sold(one,s) for s in range(100)]==[sold(two,s) for s in range(100)]


def test_location_curves_have_intended_ordering():
    def volume(loc,price):
        m={f'{loc}:SNACK:A1':dict(product_id='stroopwafel-2pk',quantity=100,price=price)}
        return sum(sum(s.units for s in demand.simulate_day(m,date(2026,1,6),'cloudy',1.,random.Random(i))[0]) for i in range(300))
    assert volume('MAIN-LOUNGE',1.8)>volume('AI-LOUNGE',1.8)
    assert volume('AI-HOUSE',1.8)>volume('AI-LOUNGE',1.8)
    assert volume('AI-HOUSE',2.4)/volume('AI-HOUSE',1.8)>volume('AI-LOUNGE',2.4)/volume('AI-LOUNGE',1.8)


def test_no_cash_box_can_block_a_sale(monkeypatch):
    """Cash is banked overnight, so nothing fills up and refuses customers."""
    monkeypatch.setattr(config,'CARD_SHARE',0.)
    e=Engine.new(1); stock(e)
    before=e.s['balance']
    e.end_of_day()
    assert e.s['units_sold']>0 and e.s['gross_revenue']>0
    assert e.s['machine']['AI-LOUNGE:SNACK:A1']['quantity']==18-e.s['units_sold']
    assert e.s['balance']==pytest.approx(before+e.s['gross_revenue']-config.DAILY_SPOT_FEE)


def test_cash_and_card_reconcile_to_sales():
    e=Engine.new(8)
    for mid in config.machine_ids():
        stock(e,mid+':A1','cola-500ml' if 'DRINKS' in mid else 'stroopwafel-2pk',100)
    e.end_of_day()
    assert e.s['gross_revenue']==pytest.approx(e.s['cash_banked']+sum(x['amount'] for x in e.s['pending_card']),abs=.01)
    assert e.s['units_sold']==sum(x['units'] for x in e.s['sales_log'][0]['per_slot'])


def test_marketing_bonus_spam_and_recovery():
    e=Engine.new(1); balance=e.s['balance']
    e.tool_run_marketing('AI-LOUNGE','slack','Fresh stock')
    assert e.s['balance']==balance-1
    assert e.marketing_traffic('AI-LOUNGE')>1
    assert e.marketing_traffic('AI-HOUSE')==1
    for i in range(8): e.tool_run_marketing('AI-LOUNGE',['prints','slack','mail'][i%3],'Fresh stock')
    assert e.marketing_traffic('AI-LOUNGE')<1
    e.s['day']+=7
    assert e.marketing_traffic('AI-LOUNGE')==1


def test_offers_stable_within_week_change_next_week():
    e=Engine.new(1); supplier=SUPPLIERS['kanaal-cash-carry']
    prices=lambda: [e.offer_price(supplier,p,100) for p in supplier.product_ids]
    first=prices(); e.s['day']=7
    assert prices()==first
    e.s['day']=8
    assert prices()!=first
    other=Engine.new(1); other.s['day']=8
    assert prices()==[other.offer_price(supplier,p,100) for p in supplier.product_ids]


def test_order_charges_current_price_without_lookup_and_locks_receipt():
    e=Engine.new(3); supplier=SUPPLIERS['kanaal-cash-carry']; pid='cola-500ml'
    price=e.offer_price(supplier,pid,200)[0]; before=e.s['balance']
    out=e.tool_order_goods(supplier.id,{pid:200})
    receipt=json.JSONDecoder().raw_decode(out)[0]
    assert receipt['paid']==round(price*200,2)
    assert e.s['balance']==pytest.approx(before-receipt['paid'])
    order=copy.deepcopy(e.s['orders'][receipt['order_id']]); e.s['day']=8
    assert e.s['orders'][receipt['order_id']]==order
    assert order['status']=='paid'


def test_checking_offers_does_not_reserve_price():
    e=Engine.new(1); supplier=SUPPLIERS['kanaal-cash-carry']; e.s['balance']=10000
    e.tool_check_offers(supplier.id,200)
    e.s['day']=8
    pid=next(p for p in supplier.product_ids if e.offer_price(supplier,p,200)[1])
    current=e.offer_price(supplier,pid,200)[0]
    receipt=json.JSONDecoder().raw_decode(e.tool_order_goods(supplier.id,{pid:200}))[0]
    assert receipt['paid']==round(current*200,2)


def test_invalid_order_is_atomic():
    e=Engine.new(1); before=e.s['balance']
    e.tool_order_goods('kanaal-cash-carry',{'cola-500ml':-10})
    assert e.s['balance']==before and not e.s['orders']


def test_finalization_seals_incomplete_run_and_scores_zero():
    e=Engine.new(1); e.s['balance']=100000
    score=e.score(finalize=True)
    assert score['reward']==0 and not score['completed']
    snapshot=copy.deepcopy(e.s)
    assert 'SIMULATION OVER' in e.tool_order_goods('kanaal-cash-carry',{'cola-500ml':200})
    assert e.s==snapshot
    assert e.score(finalize=True)==score


def test_only_complete_runs_score_and_profit_is_reported():
    e=Engine.new(1); e.s['day']=config.SIM_DAYS
    e.tool_wait_for_next_day()
    score=e.score()
    assert score['reward']==e.s['balance']
    assert score['profit']==pytest.approx(score['net_worth']-config.STARTING_BALANCE)
    # The model's own running cost is not charged to the business.
    assert 'levies_paid' not in score
    e.s['terminated']=True
    assert e.score()['reward']==0


def test_numeric_nan_cannot_corrupt_bank_or_prices():
    e=Engine.new(1); before=e.s['balance']
    e.tool_send_payment('x',float('nan'),'bad')
    e.tool_set_price('AI-LOUNGE:SNACK:A1',float('nan'))
    assert e.s['balance']==before
    assert e.s['machine']['AI-LOUNGE:SNACK:A1']['price'] is None


def test_sales_report_filters_location():
    e=Engine.new(1); stock(e); stock(e,'AI-HOUSE:SNACK:A1')
    e.end_of_day()
    report=e.tool_get_sales_report(location='AI-HOUSE')
    units=sum(s['units'] for s in e.s['sales_log'][0]['per_slot'] if s['location']=='AI-HOUSE')
    assert f'Total: {units} units' in report


def test_reference_policy_is_identical_before_and_after_json_serialization():
    from test_engine import Bot, DirectClient, sync_config
    try:
        config.reload(environ={'VENDING_SIM_DAYS':'30'})
        world=sync_config.world()
        results=[]
        for snapshot in (world,json.loads(json.dumps(world,sort_keys=True))):
            e=Engine.new(1)
            Bot(DirectClient(e),snapshot).run()
            results.append(e.score())
        assert results[0]==results[1]
    finally:
        config.reload()
