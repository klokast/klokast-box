"""The VPN gateway health checks read the existing status playbooks and request no relay test."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import vpn_egress_health as health

NOW=__import__('calendar').timegm((2026,11,17,8,26,40))


def provider(updated_at='2026-11-17T06:26:40Z',selected='JP',relays=(('JP',True,True),('SG',False,True))):
    value={'updated_at':updated_at,'server_count':len(relays),'selected_server':selected,
           'health':[{'name':n,'alive':a,'history':[{'delay':1}] if t else []} for n,a,t in relays]}
    return {'Read proxy status':{'rc':0},'Read or update the native subscription':{'stdout':json.dumps(value)}}


STATUS={'declared':True,'clients':['k001-ops','k001-air'],'replacement_needed_for_profile':False,
        'installed_routing_profile':{'domain-list-url':'https://example.invalid/gfw.txt'}}


class Health(unittest.TestCase):
    def states(self,checks):return {c['name']:c['state'] for c in checks}

    def test_a_healthy_gateway_reports_its_relays_and_selection(self):
        checks=health.service_checks(provider(),NOW)
        self.assertEqual(self.states(checks),{'Proxy service':'ok','Subscription':'ok','Relays alive':'ok','Selected relay':'ok'})
        self.assertEqual(checks[1]['detail'],'2 relays; updated 2026-11-17 06:26 UTC, 2 hours ago')
        self.assertEqual(checks[2]['detail'],'1 of 2 at the last native test round')

    def test_a_stale_subscription_and_a_dead_selection_are_warnings(self):
        checks=self.states(health.service_checks(provider(updated_at='2026-11-14T06:26:40Z',selected='SG'),NOW))
        self.assertEqual((checks['Subscription'],checks['Selected relay']),('warning','warning'))
        # Without a native test round, no relay is known to be dead.
        checks=self.states(health.service_checks(provider(relays=(('JP',False,False),)),NOW))
        self.assertEqual((checks['Relays alive'],checks['Selected relay']),('ok','ok'))

    def test_a_stopped_service_or_a_failed_provider_read_fails(self):
        self.assertEqual(health.service_checks({'Read proxy status':{'failed':True}},NOW),
                         [health.check('Proxy service','failed','the vpn-egress service is not started')])
        tasks={'Read proxy status':{},'Read or update the native subscription':{'failed':True,'stderr':'withheld'}}
        self.assertEqual(health.service_checks(tasks,NOW)[1],health.check('Subscription','failed','withheld'))

    def test_report_skips_traffic_after_a_failure_and_never_tests_an_undeclared_gateway(self):
        self.assertEqual(self.states(health.report('k001',dict(STATUS,declared=False),{},repo=Path('.'))['checks']),{'Declaration':'failed'})
        with patch.object(health,'ansible_results',return_value={'Read proxy status':{'failed':True}}), \
                patch.object(health,'traffic_checks') as traffic:
            checks=health.report('k001',STATUS,{},repo=Path('.'))['checks']
        traffic.assert_not_called();self.assertEqual(checks[-1]['state'],'skipped')
        with patch.object(health,'ansible_results',return_value=provider()), \
                patch.object(health,'traffic_checks',return_value=[health.check('Proxy traffic: Google','ok','HTTP 204')]):
            checks=health.report('k001',dict(STATUS,replacement_needed_for_profile=True),{},repo=Path('.'))['checks']
        self.assertEqual(self.states(checks)['Routing profile'],'warning');self.assertEqual(checks[-1]['name'],'Proxy traffic: Google')

    def test_results_of_both_playbooks_are_read(self):
        def document(name,result,stats=None):
            return json.dumps({'plays':[{'tasks':[{'task':{'name':name},'hosts':{'k001-vpn-egress':result}}]}],
                               'stats':{'k001-vpn-egress':stats or {}}},indent=4)
        out=document('Read proxy status',{'rc':0})+'\n'+document('Read or update the native subscription',{'stdout':'{}'})
        run=health.subprocess.CompletedProcess([],0,out,'')
        with patch.object(health.subprocess,'run',return_value=run):
            tasks=health.ansible_results('k001-vpn-egress',{},repo=Path('.'))
        self.assertEqual(sorted(tasks),['Read or update the native subscription','Read proxy status'])
        for stdout in ('','not json',document('x',{},{'unreachable':1})):
            with self.subTest(stdout=stdout[:10]),patch.object(health.subprocess,'run',
                    return_value=health.subprocess.CompletedProcess([],4,stdout,'')),self.assertRaises(RuntimeError):
                health.ansible_results('k001-vpn-egress',{},repo=Path('.'))

    def test_traffic_needs_the_same_box_controller(self):
        with patch.object(health.socket,'gethostname',return_value='k002-ops'):
            self.assertEqual(health.traffic_checks('k001')[0]['state'],'skipped')


if __name__=='__main__':unittest.main()
