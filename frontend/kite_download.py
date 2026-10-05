"""Kite authentication, historical downloads, and local dataset storage."""

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


def render_kite_download():
    st.title("Download Kite data")
    st.caption("Zerodha Kite Connect · historical one-minute OHLCV · local CSV storage")
    st.markdown(
        "Use a Kite Connect app with historical market-data access. [Kite developer console](https://developers.kite.trade/) · [Historical API documentation](https://kite.trade/docs/connect/v3/historical/)"
    )
    with st.expander("Connect to Kite", expanded="kite_client" not in st.session_state):
        api_key = st.text_input("Kite API key", type="password", key="kite_api_key")
        method = st.radio("Authentication", ["Access token", "Kite login"])
        if method == "Access token":
            token = st.text_input(
                "Access token", type="password", key="kite_access_token"
            )
            secret, request_token = "", ""
        else:
            if api_key:
                st.link_button(
                    "Open Zerodha login",
                    "https://kite.zerodha.com/connect/login?"
                    + urlencode({"v": 3, "api_key": api_key.strip()}),
                )
            st.caption(
                "After login, paste the redirect URL or request_token below. Use your app's registered redirect URL."
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
                client = kite_client(api_key)
                with st.spinner("Connecting and loading NSE instruments..."):
                    if method == "Kite login":
                        request = request_token.strip()
                        if "://" in request:
                            request = parse_qs(urlparse(request).query).get(
                                "request_token", [""]
                            )[0]
                        if not request:
                            raise ValueError(
                                "The redirect URL does not contain a request_token"
                            )
                        try:
                            session = client.generate_session(
                                request, api_secret=secret.strip()
                            )
                            client.set_access_token(session["access_token"])
                        except Exception as exc:
                            raise RuntimeError(api_error(exc)) from None
                    else:
                        client.set_access_token(token.strip())
                    try:
                        profile = client.profile()
                    except Exception as exc:
                        raise RuntimeError(api_error(exc)) from None
                    instruments = nse_instruments(client)
                st.session_state["kite_client"] = client
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
