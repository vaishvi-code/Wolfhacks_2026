import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [{'id': n} for n in 'ABCD']
        self.edges = [dict(id=a+b, a=a, b=b, distance=d, risk=r) for a,b,d,r in [('A','B',1,'high'),('B','D',1,'clear'),('A','C',3,'clear'),('C','D',3,'clear')]]

    def test_longer_route_avoids_high_risk(self):
        result = server.shortest_path(self.nodes,self.edges,'A','D')
        self.assertEqual(result['nodes'],['A','C','D'])
        self.assertEqual(result['distance'],6)

    def test_blocked_segments_excluded(self):
        self.edges[0]['risk'] = 'blocked'
        self.assertEqual(server.shortest_path(self.nodes,self.edges,'A','D')['nodes'],['A','C','D'])

    def test_disconnected_route(self):
        self.edges[0]['risk'] = self.edges[2]['risk'] = 'blocked'
        self.assertIsNone(server.shortest_path(self.nodes,self.edges,'A','D'))

    def test_at_destination(self):
        self.assertEqual(server.shortest_path(self.nodes,self.edges,'A','A')['distance'],0)

    def test_hazard_intersects_segment_not_just_endpoints(self):
        nodes = [{'id':'A','lat':35.78,'lon':-78.66},{'id':'B','lat':35.78,'lon':-78.64}]
        edges = [{'id':'AB','a':'A','b':'B','distance':1800}]
        hazards = [{'id':'h','lat':35.78,'lon':-78.65,'radius':50,'severity':'blocked'}]
        self.assertEqual(server.risk_edges(nodes,edges,hazards)[0]['risk'],'blocked')

    def test_expired_reports_ignored(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'DB',Path(folder)/'test.sqlite'):
            server.init_db()
            with server.connect() as db:
                db.execute("INSERT INTO reports VALUES (1,35.78,-78.65,'Flooding','blocked','Expired','2000-01-01','2000-01-02')")
            self.assertEqual(len(server.hazards()),3)

    def test_sensor_missing_values_not_analytics(self):
        payload={'value':{'timeSeries':[{'sourceInfo':{'siteCode':[{'value':'02087500'}],'siteName':'Neuse'},'variable':{'unit':{'unitCode':'ft'},'noDataValue':-999999},'values':[{'value':[{'value':'-999999','dateTime':'2026-10-03T12:00:00Z'},{'value':'3.5','dateTime':'2026-10-03T12:15:00Z'}]}]}]}}
        self.assertEqual([r['value'] for r in server.normalize_sensors(payload)],[3.5])

if __name__ == '__main__':
    unittest.main()
