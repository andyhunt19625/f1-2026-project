import unittest

from race_engineer import tools
from race_engineer.state import LiveState

# Event details copied from Andy's Australia race recording (13 Nov 2026,
# session 20261113T144548Z_f581fa0a7b1bc0de). Slot 0 = NORRIS (Andy), 1 PIASTRI, 7 HAMILTON, 15 HULKENBERG.
NAMES = ["NORRIS", "PIASTRI", "RUSSELL", "ANTONELLI", "VERSTAPPEN", "HADJAR", "LECLERC", "HAMILTON", "ALBON",
         "SAINZ", "LAWSON", "LINDBLAD", "STROLL", "BEARMAN", "OCON", "HULKENBERG", "BORTOLETO", "GASLY",
         "COLAPINTO", "PÉREZ", "BOTTAS", "MARTINS"]
RACE = [
    (417.020, "PENA", {"penaltyType": 5, "infringementType": 4, "vehicleIdx": 15, "otherVehicleIdx": 20,
                       "time": 255, "lapNum": 5, "placesGained": 0}),
    (417.688, "SCAR", {"safetyCarType": 1, "eventType": 0}),
    (417.793, "COLL", {"vehicle1Idx": 0, "vehicle2Idx": 7, "severity": 1}),
    (729.744, "SCAR", {"safetyCarType": 1, "eventType": 3}),
    (1014.399, "PENA", {"penaltyType": 5, "infringementType": 7, "vehicleIdx": 0, "otherVehicleIdx": 255,
                        "time": 255, "lapNum": 11, "placesGained": 0}),
    (1202.653, "RTMT", {"vehicleIdx": 15, "reason": 3}),
    (1403.252, "COLL", {"vehicle1Idx": 1, "vehicle2Idx": 0, "severity": 2}),
    (1407.259, "PENA", {"penaltyType": 5, "infringementType": 4, "vehicleIdx": 0, "otherVehicleIdx": 1,
                        "time": 255, "lapNum": 15, "placesGained": 0}),
    (1841.550, "COLL", {"vehicle1Idx": 13, "vehicle2Idx": 9, "severity": 2}),     # not involving Andy
    (1877.860, "FLBK", {"flashbackFrameIdentifier": 115677, "flashbackSessionTime": 1865.5084228515625}),
    (1927.792, "RCWN", {"vehicleIdx": 2}),
]


def lapdata(lap):
    return {"currentLapNum": lap, "numPitStops": 0, "pitLaneTimerActive": 0, "pitLaneTimeInLaneInMS": 0,
            "carPosition": 4, "lastLapTimeInMS": 0}


class EventTests(unittest.TestCase):
    def setUp(self):
        self.s = LiveState()
        self.s.ingest({"packet_name": "Participants", "numActiveCars": 22,
                       "participants": [{"aiControlled": 0 if i == 0 else 1, "name": n} for i, n in enumerate(NAMES)]})
        self.s.ingest({"packet_name": "Session", "sessionType": 15, "trackId": 0, "safetyCarStatus": 0})
        for t, code, d in RACE:
            lap = int(t // 93) + 1
            self.s.ingest({"packet_name": "LapData", "sessionTime": t, "lapData": [lapdata(lap)] * 22})
            if code == "SCAR" and d["eventType"] == 0:
                self.s.ingest({"packet_name": "Session", "sessionTime": t, "safetyCarStatus": 1})
            if code == "SCAR" and d["eventType"] == 3:
                self.s.ingest({"packet_name": "Session", "sessionTime": t, "safetyCarStatus": 0})
            self.s.ingest({"packet_name": "Event", "sessionTime": t, "eventCode": code, "eventName": code,
                           "details": d})
        self.s.ingest({"packet_name": "Event", "eventCode": "BUTN", "details": {"buttonStatus": 1}})

    def texts(self):
        return [e["text"] for e in self.s.events]

    def test_warnings_are_warnings_not_penalties(self):
        t = self.texts()
        self.assertIn("You: warning for corner cutting, gained time, lap 11.", t)
        self.assertIn("You: warning for small collision (with Piastri), lap 15.", t)
        self.assertIn("Hulkenberg: warning for small collision (with Bottas), lap 5.", t)
        self.assertFalse(any("penalty" in x.lower() for x in t))

    def test_safety_car_status_and_events(self):
        t = self.texts()
        self.assertIn("Full safety car deployed.", t)
        self.assertIn("Full safety car race resumed.", t)
        self.assertIn("Safety car status: full safety car.", t)
        self.assertIn("Safety car status: no safety car.", t)
        self.assertEqual(self.s.safety_car, 0)

    def test_only_your_contact_listed_others_counted(self):
        t = self.texts()
        self.assertIn("Contact with Hamilton (severity 1).", t)
        self.assertIn("Contact with Piastri (severity 2).", t)
        self.assertFalse(any("Bearman" in x or "Sainz" in x for x in t))
        self.assertEqual(self.s.event_counts["COLL"], 3)
        self.assertEqual(self.s.event_counts["BUTN"], 1)

    def test_retirement_flashback_winner_and_tool(self):
        t = self.texts()
        self.assertIn("Hulkenberg retired (reason 3).", t)
        self.assertIn("Flashback used, back to session time 1865.5 s.", t)
        self.assertIn("Race winner: Russell.", t)
        ans = tools.race_events(self.s)
        self.assertTrue(ans["answer"].startswith("No safety car."))
        self.assertEqual(ans["safety_car_status"], 0)


if __name__ == "__main__":
    unittest.main()
