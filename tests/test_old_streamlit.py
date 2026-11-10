import streamlit as st
import pandas as pd
import ui


def test_show_df_falls_back_on_old_streamlit(monkeypatch):
    calls = []
    def old_dataframe(df, width=None, use_container_width=False, **kw):
        if isinstance(width, str):
            raise TypeError("'str' object cannot be interpreted as an integer")
        calls.append(use_container_width)
    monkeypatch.setattr(st, "dataframe", old_dataframe)
    ui.show_df(pd.DataFrame({"a": [1]}), hide_index=True)
    assert calls == [True]
