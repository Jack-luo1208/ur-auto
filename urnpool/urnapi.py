#!/usr/bin/env python3
"""URnetwork operator API client: wallet signup, api key, balance, pinned proxy."""
import json
import time
import urllib.error
import urllib.request

from . import wallet

DEFAULT_BASE = "https://api.bringyour.com"


class ApiError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class Urn:
    def __init__(self, base=DEFAULT_BASE, timeout=45, retries=4, backoff=3.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    # ---------- transport ----------
    def _call(self, path, body=None, token=None, method="POST"):
        last = None
        for attempt in range(self.retries):
            data = None if body is None else json.dumps(body).encode()
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                text = exc.read().decode(errors="replace")
                try:
                    payload = json.loads(text)
                except Exception:
                    payload = {"_text": text[:300]}
                # 4xx other than 429 is a real answer, do not retry
                if 400 <= exc.code < 500 and exc.code != 429:
                    return payload if payload else {"_http": exc.code}
                last = ApiError(exc.code, text[:300])
            except Exception as exc:  # transport / TLS / DNS
                last = exc
            time.sleep(self.backoff * (attempt + 1))
        raise ApiError(0, f"unreachable: {last}")

    # ---------- account ----------
    def signup(self, network_name=None):
        """Create a network with a fresh Solana keypair. No email, no phone, no card."""
        kp = wallet.new_keypair()
        challenge = self._call("/auth/wallet-challenge", {"blockchain": "solana", "wallet_address": kp["address"]})
        message = challenge["message_template"]
        created = self._call("/auth/network-create", {
            "terms": True,
            "network_name": network_name or ("urnpool" + str(int(time.time()))[-6:]),
            "user_name": "",
            "product_updates": False,
            "result_errors": True,
            "wallet_auth": {
                "blockchain": "solana",
                "wallet_address": kp["address"],
                "wallet_message": message,
                "wallet_signature": wallet.sign_b64(kp["seed_b64"], message),
            },
        })
        net = (created or {}).get("network") or {}
        if not net.get("by_jwt"):
            raise ApiError(0, "signup failed: " + json.dumps(created)[:200])
        return {
            "wallet_address": kp["address"],
            "seed_b64": kp["seed_b64"],
            "by_jwt": net["by_jwt"],
            "network_id": net.get("network_id"),
            "network_name": net.get("network_name"),
        }

    def create_api_key(self, by_jwt, name="urnpool"):
        res = self._call("/account/api-key", {"name": name}, token=by_jwt)
        if not res.get("api_key"):
            raise ApiError(0, "api key failed: " + json.dumps(res)[:200])
        return res["api_key"]

    def balance(self, api_key):
        res = self._call("/subscription/balance", None, token=api_key, method="GET")
        return {
            "start_bytes": int(res.get("start_balance_byte_count") or 0),
            "remaining_bytes": int(res.get("balance_byte_count") or 0),
            "open_bytes": int(res.get("open_transfer_byte_count") or 0),
            "window_end": ((res.get("active_transfer_balances") or [{}])[0]).get("end_time"),
        }

    def network_user(self, api_key):
        return self._call("/network/user", None, token=api_key, method="GET")

    # ---------- providers / proxies ----------
    def find_locations(self, query):
        res = self._call("/network/find-provider-locations", {"query": query})
        return res.get("locations") or []

    def find_providers(self, location_id, count=8, exclude=None):
        body = {"specs": [{"location_id": location_id}], "count": count}
        if exclude:
            body["exclude_client_ids"] = list(exclude)[:200]
        res = self._call("/network/find-providers2", body)
        return res.get("providers") or []

    def auth_client(self, api_key, provider_client_id):
        res = self._call("/network/auth-client", {"proxy_config": {"initial_device_state": {
            "location": {"connect_location_id": {"client_id": provider_client_id}}}}}, token=api_key)
        return res.get("proxy_config_result") or {}

    def remove_client(self, api_key, client_id):
        return self._call("/network/remove-client", {"client_id": client_id}, token=api_key)

    def proxies(self, api_key):
        res = self._call("/network/proxies", None, token=api_key, method="GET")
        return res.get("clients") or []
