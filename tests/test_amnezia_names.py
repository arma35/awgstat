from __future__ import annotations

import unittest

from amnezia_names import parse_clients_table


class AmneziaNamesTests(unittest.TestCase):
    def test_parses_client_id_and_name(self) -> None:
        payload = r'''
        [
          {
            "clientId": "abc+/123=",
            "userData": {
              "allowed_ips": "10.8.1.2/32",
              "clientName": "valarma"
            }
          }
        ]
        '''
        self.assertEqual(parse_clients_table(payload), {"abc+/123=": "valarma"})

    def test_ignores_entries_without_name(self) -> None:
        payload = r'''
        [
          {"clientId": "peer-a=", "userData": {"creationDate": "today"}},
          {"clientId": "peer-b=", "userData": {"clientName": "phone"}}
        ]
        '''
        self.assertEqual(parse_clients_table(payload), {"peer-b=": "phone"})

    def test_sanitizes_name_for_names_map(self) -> None:
        payload = r'''
        [
          {"clientId": "peer-a=", "userData": {"clientName": "  Admin\n# nitro  "}}
        ]
        '''
        self.assertEqual(parse_clients_table(payload), {"peer-a=": "Admin nitro"})

    def test_rejects_non_array_root(self) -> None:
        with self.assertRaises(ValueError):
            parse_clients_table('{"clientId":"peer-a="}')


if __name__ == "__main__":
    unittest.main()
