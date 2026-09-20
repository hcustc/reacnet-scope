"""Local Plotly presentation shared by layouts and callbacks."""

from typing import Any


def empty_chart_figure(title: str, hint: str) -> dict[str, Any]:
    """Return an intentional empty state instead of Plotly's default axes."""
    return {
        "data": [],
        "layout": {
            "autosize": True,
            "paper_bgcolor": "#ffffff",
            "plot_bgcolor": "#ffffff",
            "margin": {"l": 24, "r": 24, "t": 24, "b": 24},
            "xaxis": {"visible": False, "fixedrange": True, "range": [0, 1]},
            "yaxis": {"visible": False, "fixedrange": True, "range": [0, 1]},
            "annotations": [
                {
                    "x": 0.5,
                    "y": 0.53,
                    "xref": "paper",
                    "yref": "paper",
                    "showarrow": False,
                    "align": "center",
                    "text": (
                        f"<b>{title}</b><br>"
                        f"<span style='font-size:12px;color:#718096'>{hint}</span>"
                    ),
                    "font": {"family": "Inter, sans-serif", "size": 15, "color": "#25324b"},
                }
            ],
        },
    }
