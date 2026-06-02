"""Lightweight in-process cron-style background tasks for Dash.

These are explicitly *not* k8s CronJobs — they ride on the FastAPI lifespan
loop. Heavy / multi-pod cron should still be split into dedicated jobs.
"""
