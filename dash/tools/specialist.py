"""
Specialist Analysis Tools
=========================

Statistical analysis tools for the Analyst agent.
Each tool handles a specific analysis type with statistical rigor.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def detect_anomalies(table: str, column: str, method: str = "zscore", threshold: float = 2.0, _engine=None, _schema: str = "public") -> str:
    """Detect anomalies/outliers in a numeric column using Z-score or IQR method.

    Args:
        table: Table name
        column: Numeric column to analyze
        method: 'zscore' or 'iqr'
        threshold: Z-score threshold (default 2.0) or IQR multiplier (default 1.5)
    """
    if not _engine:
        return json.dumps({"error": "No database connection"})
    try:
        import pandas as pd
        import numpy as np
        from sqlalchemy import text

        with _engine.connect() as conn:
            df = pd.read_sql(text(f'SELECT * FROM "{_schema}"."{table}" LIMIT 10000'), conn)

        if column not in df.columns:
            return json.dumps({"error": f"Column '{column}' not found in {table}"})

        values = pd.to_numeric(df[column], errors='coerce').dropna()
        if len(values) < 5:
            return json.dumps({"error": "Need at least 5 numeric values"})

        mean = float(values.mean())
        std = float(values.std())
        median = float(values.median())

        if method == "iqr":
            q1 = float(values.quantile(0.25))
            q3 = float(values.quantile(0.75))
            iqr = q3 - q1
            lower = q1 - threshold * iqr
            upper = q3 + threshold * iqr
            outlier_mask = (values < lower) | (values > upper)
        else:
            lower = mean - threshold * std
            upper = mean + threshold * std
            outlier_mask = ((values - mean).abs() / (std + 1e-10)) > threshold

        outliers = df[outlier_mask.reindex(df.index, fill_value=False)]

        return json.dumps({
            "status": "ok",
            "table": table, "column": column, "method": method,
            "stats": {"mean": round(mean, 2), "std": round(std, 2), "median": round(median, 2)},
            "bounds": {"lower": round(lower, 2), "upper": round(upper, 2)},
            "total_rows": len(values),
            "anomaly_count": int(outlier_mask.sum()),
            "anomaly_pct": round(float(outlier_mask.sum()) / len(values) * 100, 1),
            "anomalies": outliers.head(10).to_dict('records'),
        })
    except Exception as e:
        return json.dumps({"error": str(e)[:200]})


def run_pareto(table: str, category_col: str, value_col: str, _engine=None, _schema: str = "public") -> str:
    """Run Pareto (80/20) analysis — which categories drive most of the value.

    Args:
        table: Table name
        category_col: Column with categories (e.g., 'region', 'product')
        value_col: Numeric column to sum (e.g., 'revenue', 'amount')
    """
    if not _engine:
        return json.dumps({"error": "No database connection"})
    try:
        import pandas as pd
        from sqlalchemy import text

        with _engine.connect() as conn:
            df = pd.read_sql(text(
                f'SELECT "{category_col}", SUM(CAST("{value_col}" AS NUMERIC)) as total '
                f'FROM "{_schema}"."{table}" GROUP BY "{category_col}" ORDER BY total DESC'
            ), conn)

        if df.empty:
            return json.dumps({"error": "No data found"})

        grand_total = float(df['total'].sum())
        df['pct'] = (df['total'] / grand_total * 100).round(1)
        df['cumulative_pct'] = df['pct'].cumsum().round(1)

        # Find 80% threshold
        top_80 = df[df['cumulative_pct'] <= 80]
        if len(top_80) == 0:
            top_80 = df.head(1)

        return json.dumps({
            "status": "ok",
            "table": table, "category_col": category_col, "value_col": value_col,
            "grand_total": round(grand_total, 2),
            "total_categories": len(df),
            "top_80_count": len(top_80),
            "top_80_pct": f"{len(top_80)}/{len(df)} categories = {round(len(top_80)/len(df)*100)}%",
            "concentration": "high" if len(top_80) <= len(df) * 0.3 else "moderate" if len(top_80) <= len(df) * 0.5 else "low",
            "breakdown": df.head(15).to_dict('records'),
        })
    except Exception as e:
        return json.dumps({"error": str(e)[:200]})


def compare_periods(table: str, date_col: str, value_col: str, group_col: str = "", _engine=None, _schema: str = "public") -> str:
    """Compare current period vs previous period — auto-detects period granularity.

    Args:
        table: Table name
        date_col: Date column
        value_col: Numeric column to compare
        group_col: Optional grouping column (e.g., 'region')
    """
    if not _engine:
        return json.dumps({"error": "No database connection"})
    try:
        import pandas as pd
        from sqlalchemy import text

        with _engine.connect() as conn:
            df = pd.read_sql(text(f'SELECT * FROM "{_schema}"."{table}" LIMIT 10000'), conn)

        df[date_col] = pd.to_datetime(df[date_col], format='mixed', errors='coerce')
        df = df.dropna(subset=[date_col])
        df[value_col] = pd.to_numeric(df[value_col], errors='coerce')
        df = df.dropna(subset=[value_col])

        if len(df) < 2:
            return json.dumps({"error": "Need at least 2 rows with valid dates and values"})

        # Auto-detect period
        date_range = (df[date_col].max() - df[date_col].min()).days
        if date_range <= 60:
            period = 'W'
            period_name = 'week'
        elif date_range <= 365:
            period = 'M'
            period_name = 'month'
        else:
            period = 'Q'
            period_name = 'quarter'

        df['period'] = df[date_col].dt.to_period(period).astype(str)
        periods = sorted(df['period'].unique())

        if len(periods) < 2:
            return json.dumps({"error": "Need at least 2 periods to compare"})

        current = periods[-1]
        previous = periods[-2]

        if group_col and group_col in df.columns:
            curr_data = df[df['period'] == current].groupby(group_col)[value_col].sum()
            prev_data = df[df['period'] == previous].groupby(group_col)[value_col].sum()
            comparison = pd.DataFrame({'current': curr_data, 'previous': prev_data}).fillna(0)
            comparison['change'] = comparison['current'] - comparison['previous']
            comparison['change_pct'] = ((comparison['change'] / (comparison['previous'] + 1e-10)) * 100).round(1)
            result_data = comparison.reset_index().to_dict('records')
        else:
            curr_total = float(df[df['period'] == current][value_col].sum())
            prev_total = float(df[df['period'] == previous][value_col].sum())
            change = curr_total - prev_total
            change_pct = (change / (prev_total + 1e-10)) * 100
            result_data = [{"current": round(curr_total, 2), "previous": round(prev_total, 2), "change": round(change, 2), "change_pct": round(change_pct, 1)}]

        return json.dumps({
            "status": "ok",
            "table": table, "period_type": period_name,
            "current_period": current, "previous_period": previous,
            "comparison": result_data,
        })
    except Exception as e:
        return json.dumps({"error": str(e)[:200]})


def root_cause_drill(table: str, metric_col: str, dimension_cols: str, _engine=None, _schema: str = "public") -> str:
    """Find which dimension drives a metric — drills down automatically.

    Args:
        table: Table name
        metric_col: Numeric column (e.g., 'revenue', 'churn_rate')
        dimension_cols: Comma-separated dimension columns (e.g., 'region,product,plan')
    """
    if not _engine:
        return json.dumps({"error": "No database connection"})
    try:
        import pandas as pd
        import numpy as np
        from sqlalchemy import text

        with _engine.connect() as conn:
            df = pd.read_sql(text(f'SELECT * FROM "{_schema}"."{table}" LIMIT 10000'), conn)

        df[metric_col] = pd.to_numeric(df[metric_col], errors='coerce')
        dims = [d.strip() for d in dimension_cols.split(',') if d.strip() in df.columns]

        if not dims:
            return json.dumps({"error": f"No valid dimension columns found: {dimension_cols}"})

        overall_mean = float(df[metric_col].mean())
        results = []

        for dim in dims:
            grouped = df.groupby(dim)[metric_col].agg(['mean', 'sum', 'count']).reset_index()
            grouped['deviation'] = ((grouped['mean'] - overall_mean) / (overall_mean + 1e-10) * 100).round(1)
            grouped = grouped.sort_values('deviation', key=abs, ascending=False)

            top_driver = grouped.iloc[0] if len(grouped) > 0 else None
            results.append({
                "dimension": dim,
                "top_driver": {dim: str(top_driver[dim]), "mean": round(float(top_driver['mean']), 2), "deviation": f"{float(top_driver['deviation']):+.1f}%"} if top_driver is not None else None,
                "breakdown": grouped.head(8).to_dict('records'),
            })

        # Sort by impact
        results.sort(key=lambda r: abs(float(r['top_driver']['deviation'].replace('%','').replace('+',''))) if r.get('top_driver') else 0, reverse=True)

        return json.dumps({
            "status": "ok",
            "table": table, "metric": metric_col,
            "overall_mean": round(overall_mean, 2),
            "root_causes": results,
        })
    except Exception as e:
        return json.dumps({"error": str(e)[:200]})


def scenario_model(table: str, metric_col: str, change_pct: float, group_col: str = "", _engine=None, _schema: str = "public") -> str:
    """What-if scenario — recalculate totals with a percentage change applied.

    Args:
        table: Table name
        metric_col: Numeric column to model
        change_pct: Percentage change to apply (e.g., -10 for 10% decrease)
        group_col: Optional group column to see impact per group
    """
    if not _engine:
        return json.dumps({"error": "No database connection"})
    try:
        import pandas as pd
        from sqlalchemy import text

        with _engine.connect() as conn:
            df = pd.read_sql(text(f'SELECT * FROM "{_schema}"."{table}" LIMIT 10000'), conn)

        df[metric_col] = pd.to_numeric(df[metric_col], errors='coerce')
        current_total = float(df[metric_col].sum())
        new_total = current_total * (1 + change_pct / 100)
        impact = new_total - current_total

        result = {
            "status": "ok",
            "table": table, "metric": metric_col,
            "change_applied": f"{change_pct:+.1f}%",
            "current_total": round(current_total, 2),
            "projected_total": round(new_total, 2),
            "absolute_impact": round(impact, 2),
        }

        if group_col and group_col in df.columns:
            grouped = df.groupby(group_col)[metric_col].sum().reset_index()
            grouped['projected'] = (grouped[metric_col] * (1 + change_pct / 100)).round(2)
            grouped['impact'] = (grouped['projected'] - grouped[metric_col]).round(2)
            result["by_group"] = grouped.to_dict('records')

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)[:200]})


def benchmark_check(table: str, metric_col: str, target: float, group_col: str = "", _engine=None, _schema: str = "public") -> str:
    """Compare actual values against a target/benchmark.

    Args:
        table: Table name
        metric_col: Numeric column to check
        target: Target value to compare against
        group_col: Optional group column for per-group comparison
    """
    if not _engine:
        return json.dumps({"error": "No database connection"})
    try:
        import pandas as pd
        from sqlalchemy import text

        with _engine.connect() as conn:
            df = pd.read_sql(text(f'SELECT * FROM "{_schema}"."{table}" LIMIT 10000'), conn)

        df[metric_col] = pd.to_numeric(df[metric_col], errors='coerce')

        actual = float(df[metric_col].mean())
        gap = actual - target
        gap_pct = (gap / (target + 1e-10)) * 100
        status = "above" if gap > 0 else "below" if gap < 0 else "on_target"

        result = {
            "status": "ok",
            "table": table, "metric": metric_col,
            "target": target,
            "actual_mean": round(actual, 2),
            "gap": round(gap, 2),
            "gap_pct": f"{gap_pct:+.1f}%",
            "verdict": status,
        }

        if group_col and group_col in df.columns:
            grouped = df.groupby(group_col)[metric_col].mean().reset_index()
            grouped['target'] = target
            grouped['gap'] = (grouped[metric_col] - target).round(2)
            grouped['status'] = grouped['gap'].apply(lambda g: 'above' if g > 0 else 'below')
            result["by_group"] = grouped.to_dict('records')
            result["groups_above"] = int((grouped['gap'] > 0).sum())
            result["groups_below"] = int((grouped['gap'] < 0).sum())

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)[:200]})


def correlation_matrix(table: str, columns: str, _engine=None, _schema: str = "public") -> str:
    """Find correlations between numeric columns.

    Args:
        table: Table name
        columns: Comma-separated numeric column names (e.g., 'revenue,units,price')
    """
    if not _engine:
        return json.dumps({"error": "No database connection"})
    try:
        import pandas as pd
        import numpy as np
        from sqlalchemy import text

        with _engine.connect() as conn:
            df = pd.read_sql(text(f'SELECT * FROM "{_schema}"."{table}" LIMIT 10000'), conn)

        cols = [c.strip() for c in columns.split(',') if c.strip() in df.columns]
        if len(cols) < 2:
            return json.dumps({"error": f"Need at least 2 valid numeric columns. Found: {cols}"})

        for c in cols:
            df[c] = pd.to_numeric(df[c], errors='coerce')

        corr = df[cols].corr()

        # Find strongest correlations
        pairs = []
        for i in range(len(cols)):
            for j in range(i+1, len(cols)):
                r = float(corr.iloc[i, j])
                if not np.isnan(r):
                    strength = "strong" if abs(r) > 0.7 else "moderate" if abs(r) > 0.4 else "weak"
                    direction = "positive" if r > 0 else "negative"
                    pairs.append({
                        "col_a": cols[i], "col_b": cols[j],
                        "correlation": round(r, 3),
                        "strength": strength, "direction": direction,
                    })

        pairs.sort(key=lambda p: abs(p['correlation']), reverse=True)

        return json.dumps({
            "status": "ok",
            "table": table,
            "columns_analyzed": cols,
            "pairs": pairs,
            "matrix": {col: {c: round(float(corr.loc[col, c]), 3) for c in cols} for col in cols},
        })
    except Exception as e:
        return json.dumps({"error": str(e)[:200]})
