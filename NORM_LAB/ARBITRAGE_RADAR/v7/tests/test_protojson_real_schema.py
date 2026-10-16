"""Offline protocol examples built from official Finam protobuf field names.

No credentials, no accounts, no real broker tape, and no network.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import unittest
from unittest.mock import patch
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import NORM_ARB_WS_SHAPE_V2 as mod

RTSX=mod.BOOK_SYMBOL


def data(v):
    return {"type":"DATA","subscription_type":"ORDER_BOOK","timestamp":1792160001,
            "payload":json.dumps(v, ensure_ascii=True)}


def pbook(*rows, snap=False):
    return {"orderBook":[{"symbol":RTSX, "rows":list(rows),"isDataSnapshot":snap}]}


class ProtoJsonWireTests(unittest.TestCase):
    def test_actual_protojson_case_wire_payload(self):
        c=Counter()
        ev=mod.interpret_data(data(pbook(
          {"price":"1025", "buySize":"18", "action":"ACTION_ADD", "timestamp":"2026-10-16T14:25:01Z"},
          {"price":"1026", "sellSize":"82", "action":"ACTION_ADD", "timestamp":"2026-10-16T14:25:01Z"}, snap=True)),c)
        self.assertIsNotNone(ev)
        self.assertEqual(ev['items'][0]['is_data_snapshot'],True)
        self.assertEqual(ev['items'][0]['rows'][0]['buy_size'],'18')
        self.assertNotIn('sell_size',ev['items'][0]['rows'][0])
        self.assertEqual(ev['items'][0]['rows'][1]['sell_size'],'82')
        self.assertEqual(c['book_rows_sanitized'],2)
        self.assertEqual(c['protojson_camel_field_isDataSnapshot'],1)
        self.assertEqual(c['payload_string_json_to_object'],1)
    def test_delivered_protobuf_camel_form_price_is_sanitized(self):
        obj=pbook({"price":{"value":"1025"},"buySize":{"value":"3"},"action":"ACTION_UPDATE"})
        e=mod.interpret_data(data(obj),Counter())
        self.assertEqual(e['items'][0]['rows'][0]['buy_size'],'3')
        self.assertEqual(e['items'][0]['rows'][0]['price'],'1025')
    def test_snake_proto_name_supported(self):
        obj={"order_book":[{"symbol":RTSX,"rows":[{"price":"-0.018","sell_size":"5","action":"ACTION_ADD"}],"is_data_snapshot":False}]}
        e=mod.interpret_data(data(obj),Counter())
        self.assertEqual(e['items'][0]['rows'][0]['price'],'-0.018')
        self.assertEqual(e['items'][0]['is_data_snapshot'],False)
    def test_conflicting_camel_and_snake_cannot_choose_random_side(self):
        obj=pbook({"price":"1025","buy_size":"30","buySize":"40","action":"ACTION_UPDATE"})
        c=Counter();ev=mod.interpret_data(data(obj),c)
        self.assertIsNone(ev)
        self.assertGreater(c['conflicting_protojson_alias'],0)
    def test_protojson_secret_field_is_discarded(self):
        secret='HIDDEN_SECRET_STRING_EXAMPLE';obj=pbook({"price":"1025","buySize":"3","action":"ACTION_ADD", "accountId":secret})
        obj['jwt']=secret;obj['orderBook'][0]['token']=secret
        e=mod.interpret_data(data(obj),Counter())
        self.assertNotIn(secret,json.dumps(e))
    def test_protojson_rejects_account_subscriptions(self):
        e=mod.interpret_data({"type":"DATA","subscription_type":"ACCOUNT","payload":json.dumps(pbook({"price":"1","buySize":"3"}))},Counter())
        self.assertIsNone(e)
    def test_protojson_quote_case_still_works(self):
        e=mod.interpret_data({"type":"DATA","subscription_type":"QUOTES","payload":json.dumps({"quote":[{"symbol":"SBER@MISX","ask":"290","bid":"289.99"}]})},Counter())
        self.assertEqual(e['items'][0]['bid'],'289.99')

if __name__ == '__main__': unittest.main()