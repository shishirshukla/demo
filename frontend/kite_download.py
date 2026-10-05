"""Kite authentication, historical downloads, and local dataset storage."""

import hashlib
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import pandas as pd
import streamlit as st

from trading_system.data.kite_downloader import (
    STORAGE_ROOT,
    api_error,
    download_kite_minutes,
    kite_client,
    nse_instruments,
    save_kite_download,
)


def capture_kite_callback():
    """Read a browser return before widgets, then remove auth data from the URL."""
    params = st.query_params
    request = params.get("request_token", "")
    failed = params.get("action") == "login" and params.get("status") in {
        "error",
        "failed",
        "cancelled",
    }
    if not request and not failed:
        return
    st.session_state["workspace_menu"] = "Download Kite data"
    st.session_state["kite_auth_method"] = "Kite login"
    if request and params.get("status", "success") == "success":
        st.session_state["kite_request_token"] = request
        st.session_state["kite_callback_received"] = True
        st.session_state.pop("kite_callback_error", None)
    else:
        st.session_state["kite_callback_error"] = (
            "Zerodha login was cancelled or failed. Open Zerodha login again."
        )
    for name in ("request_token", "status", "action"):
        if name in params:
            del params[name]


def extract_request_token(value):
    value = value.strip()
    if "://" in value or value.startswith("?") or "request_token=" in value:
        query = urlparse(value).query if "://" in value else value.lstrip("?")
        params = parse_qs(query)
        if params.get("status", ["success"])[0] != "success":
            raise ValueError("Zerodha login did not succeed. Start a new login.")
        tokens = params.get("request_token", [])
        if len(tokens) != 1 or not tokens[0].strip():
            raise ValueError("The redirect URL must contain one request_token")
        return tokens[0].strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("Enter the request_token from a successful Zerodha login")
    return value


def exchange_login(client, request, secret):
    try:
        session = client.generate_session(request, api_secret=secret.strip())
        client.set_access_token(session["access_token"])
    except Exception as exc:
        message = str(exc).lower()
        if "checksum" in message or "api_secret" in message or "api secret" in message:
            detail = "Kite rejected the API secret/checksum. Use the API key and API secret from the same Kite Connect app, not your Zerodha account password. Start a fresh Zerodha login after correcting them."
        elif "api_key" in message or "api key" in message:
            detail = "Kite rejected the API key. Copy it from your active Kite Connect app and start a fresh Zerodha login with that key."
        elif (
            "request_token" in message
            or "request token" in message
            or type(exc).__name__ in {"TokenException", "InputException"}
        ):
            detail = "The request token is invalid, expired, or already used. Open Zerodha login again and connect promptly using the new token and the matching app's API secret."
        else:
            detail = api_error(exc)
        # Show diagnostic type/status, never raw provider messages or credentials.
        code = getattr(exc, "code", None)
        diagnostic = type(exc).__name__ + (
            f", HTTP {code}" if isinstance(code, int) else ""
        )
        raise RuntimeError(
            f"Kite token exchange failed ({diagnostic}). {detail}"
        ) from None
    return client


def render_kite_download():
    st.title("Download Kite data")
    st.caption("Zerodha Kite Connect · historical one-minute OHLCV · local CSV storage")
    st.markdown(
        "Use a Kite Connect app with historical market-data access. [Kite developer console](https://developers.kite.trade/) · [Historical API documentation](https://kite.trade/docs/connect/v3/historical/)"
    )
    if st.session_state.get("kite_callback_error"):
        st.error(st.session_state["kite_callback_error"])
    if (
        st.session_state.get("kite_callback_received")
        and "kite_client" not in st.session_state
    ):
        st.info(
            "Zerodha returned a login token. Enter your API key and secret in this tab, then click Connect to Kite promptly. A new tab has a separate dashboard session."
        )
    with st.expander("Connect to Kite", expanded="kite_client" not in st.session_state):
        api_key = st.text_input("Kite API key", type="password", key="kite_api_key")
        method = st.radio(
            "Authentication", ["Access token", "Kite login"], key="kite_auth_method"
        )
        if method == "Access token":
            token = st.text_input(
                "Access token", type="password", key="kite_access_token"
            )
            secret, request_token = "", ""
        else:
            st.markdown(
                "**Set your Kite app's registered redirect URL to the dashboard address you use:**"
            )
            st.code("http://localhost:8501/", language=None)
            st.caption(
                "If you open the dashboard at 127.0.0.1 or a different port, register that exact address instead. The callback opens the Kite menu automatically; enter credentials again if it returns in a new tab."
            )
            if api_key:
                st.link_button(
                    "Open Zerodha login",
                    "https://kite.zerodha.com/connect/login?"
                    + urlencode({"v": 3, "api_key": api_key.strip()}),
                )
            st.caption(
                "After login, this dashboard captures the request token automatically. If you use another redirect URL, paste its full URL or request_token below."
            )
            secret = st.text_input("API secret", type="password", key="kite_api_secret")
            request_token = st.text_input(
                "Request token or redirect URL",
                type="password",
                key="kite_request_token",
            )
            token = ""
        st.caption(
            "Credentials stay in this dashboard session and are not saved to disk. Kite access tokens expire; reconnect when required."
        )
        if st.button("Connect to Kite", type="primary"):
            for key in (
                "kite_client",
                "kite_instruments",
                "kite_download",
                "kite_user",
            ):
                st.session_state.pop(key, None)
            try:
                if method == "Access token" and not token.strip():
                    raise ValueError("Enter an access token")
                if method == "Kite login" and (
                    not secret.strip() or not request_token.strip()
                ):
                    raise ValueError("Enter the API secret and request token")
                request = (
                    extract_request_token(request_token)
                    if method == "Kite login"
                    else token.strip()
                )
                signature = hashlib.sha256(
                    (method + "\0" + api_key.strip() + "\0" + request).encode()
                ).hexdigest()
                # Reuse an exchanged token when account/instrument loading failed.
                client = (
                    st.session_state.get("kite_pending_client")
                    if st.session_state.get("kite_pending_signature") == signature
                    else None
                )
                with st.spinner("Connecting and loading NSE instruments..."):
                    if client is None:
                        client = kite_client(api_key)
                        if method == "Kite login":
                            exchange_login(client, request, secret)
                        else:
                            client.set_access_token(request)
                        st.session_state["kite_pending_client"] = client
                        st.session_state["kite_pending_signature"] = signature
                    try:
                        profile = client.profile()
                    except Exception as exc:
                        raise RuntimeError(
                            "Kite account verification failed. " + api_error(exc)
                        ) from None
                    try:
                        instruments = nse_instruments(client)
                    except (ValueError, RuntimeError) as exc:
                        raise RuntimeError(
                            "Login succeeded, but NSE instruments could not be loaded. Click Connect to Kite to retry without logging in again. "
                            + str(exc)
                        ) from None
                st.session_state["kite_client"] = client
                st.session_state.pop("kite_callback_received", None)
                st.session_state["kite_user"] = profile.get("user_id", "Kite user")
                st.session_state["kite_instruments"] = instruments
                st.rerun()
            except (ValueError, RuntimeError) as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(api_error(exc))
    if "kite_client" in st.session_state:
        st.success(f"Connected as {st.session_state['kite_user']}")
        if st.button("Disconnect and clear credentials"):
            for key in list(st.session_state):
                if key.startswith("kite_"):
                    del st.session_state[key]
            st.rerun()
        instruments = st.session_state["kite_instruments"]
        tokens = instruments.instrument_token.astype(int).tolist()
        labels = {
            int(r.instrument_token): f"{r.tradingsymbol} · {r.get('name', '')}"
            for _, r in instruments.iterrows()
        }
        defaults = (
            instruments.loc[instruments.tradingsymbol == "RELIANCE", "instrument_token"]
            .astype(int)
            .tolist()
        )
        today = pd.Timestamp.now(tz="Asia/Kolkata").date()
        with st.form("kite_download_form"):
            selected = st.multiselect(
                "NSE instruments",
                tokens,
                default=defaults,
                format_func=lambda t: labels[t],
            )
            left, right = st.columns(2)
            start = left.date_input(
                "Start date (inclusive)", today - timedelta(days=30), max_value=today
            )
            end = right.date_input("End date (inclusive)", today, max_value=today)
            st.text_input("Candle interval", "1 minute", disabled=True)
            label = st.text_input(
                "Dataset label (optional)",
                help="Letters, digits, underscores and hyphens",
            )
            st.caption(
                f"Saves a new dataset folder under {STORAGE_ROOT}. Existing downloads are preserved."
            )
            submitted = st.form_submit_button(
                "Download and save locally", type="primary"
            )
        if submitted:
            st.session_state.pop("kite_download", None)
            status = None
            try:
                if label and not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", label):
                    raise ValueError(
                        "Use up to 60 letters, digits, underscores or hyphens for the label"
                    )
                status = st.progress(0, text="Preparing download...")
                result = download_kite_minutes(
                    st.session_state["kite_client"],
                    instruments.loc[instruments.instrument_token.isin(selected)],
                    start,
                    end,
                    progress=lambda fraction, message: status.progress(
                        fraction, text=message
                    ),
                )
                save_kite_download(result, label)
                st.session_state["kite_download"] = result
            except (ValueError, RuntimeError) as exc:
                st.error(str(exc))
            except OSError:
                st.error(
                    "Could not save the dataset. Check disk space and folder permissions."
                )
            except Exception as exc:
                st.error(api_error(exc))
            finally:
                if status is not None:
                    status.empty()
    else:
        st.info(
            "Connect to Kite to choose instruments and download historical candles."
        )
    result = st.session_state.get("kite_download")
    if result is not None:
        st.success(f"Saved {len(result.candles):,} one-minute candles locally")
        st.code(str(result.path), language=None)
        st.caption(
            "Timestamps are candle starts in Asia/Kolkata. Live minute excluded; missing minutes not filled. The backtester requires five-minute bars and daily context."
        )
        st.download_button(
            "Download a ZIP copy",
            result.to_zip(),
            "kite_1minute.zip",
            "application/zip",
        )
        st.dataframe(result.candles.head(1000), hide_index=True, width="stretch")
        with st.expander("Download coverage"):
            st.json(result.manifest)
    with st.expander("Saved local datasets"):
        files = sorted(STORAGE_ROOT.glob("*/download_manifest.json"), reverse=True)
        if not files:
            st.caption("No saved datasets yet.")
        for manifest in files[:30]:
            st.code(str(manifest.parent), language=None)
