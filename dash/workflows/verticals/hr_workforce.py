"""HR / Workforce vertical pack.

Detects: project schemas w/ employee, hire, termination, comp history, or
org-unit tables. Targets headcount summary, attrition analysis, span-of-
control audit, comp equity check, time-to-fill, new-hire ramp.

Two formats:
  PACK     — legacy {placeholder} template_sql, install-time bind.
  MDL_PACK — WrenAI-style logical model w/ virtual cols + relationships.
"""
PACK = {
    "name": "hr_workforce",
    "vertical": "HR / Workforce",
    "description": "Headcount + attrition + span of control + comp equity workflows",
    "detect": {
        "required_tables_any": ["employees", "employee", "hires", "new_hires",
                                "terminations", "exits", "payroll",
                                "comp_history", "compensation", "org_units",
                                "departments", "time_punches"],
        "required_cols_any":   ["employee_id", "hire_date", "termination_date",
                                "manager_id", "department", "salary",
                                "annual_salary", "title", "job_title"],
    },
    "workflows": [
        {
            "name": "Headcount Summary",
            "description": "Active headcount per org unit",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["employees", "employee"]},
                "cols": {
                    "org": ["department", "org_unit", "team", "dept"],
                    "emp": ["employee_id", "emp_id", "id"],
                },
            },
            "template_sql": "SELECT {org}, COUNT(DISTINCT {emp}) AS headcount "
                            "FROM {table} "
                            "GROUP BY {org} ORDER BY headcount DESC LIMIT 100",
        },
        {
            "name": "Attrition Last 90 Days",
            "description": "Termination volume per org over recent period",
            "action": "alert",
            "expects": {
                "table": {"aliases": ["terminations", "exits"]},
                "cols": {
                    "org": ["department", "org_unit", "team", "dept"],
                    "term_date": ["termination_date", "exit_date", "end_date"],
                },
            },
            "template_sql": "SELECT {org}, COUNT(*) AS terms "
                            "FROM {table} "
                            "WHERE {term_date} >= CURRENT_DATE - INTERVAL '90 days' "
                            "GROUP BY {org} ORDER BY terms DESC LIMIT 50",
        },
        {
            "name": "Span of Control Audit",
            "description": "Managers w/ unusually large or small direct-report counts",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["employees", "employee"]},
                "cols": {
                    "manager": ["manager_id", "mgr_id", "reports_to"],
                },
            },
            "template_sql": "SELECT {manager}, COUNT(*) AS direct_reports "
                            "FROM {table} "
                            "WHERE {manager} IS NOT NULL "
                            "GROUP BY {manager} "
                            "ORDER BY direct_reports DESC LIMIT 100",
        },
    ],
}


# ───────────────────────── MDL FORMAT (Phase 3) ───────────────────────────
MDL_PACK = {
    "name": "hr_workforce_mdl",
    "vertical": "HR / Workforce",
    "description": "MDL-format HR pack. Logical `employees`, `hires`, "
                   "`terminations`, `comp_history`, `org_units` models. "
                   "Workforce analytics portable across HRIS schemas.",
    "detect": {
        "required_tables_any": ["employees", "employee", "hires", "new_hires",
                                "terminations", "exits", "payroll",
                                "comp_history", "compensation", "org_units",
                                "departments", "time_punches"],
        "required_cols_any":   ["employee_id", "hire_date", "termination_date",
                                "manager_id", "department", "salary",
                                "annual_salary", "title", "job_title"],
    },
    "models": [
        {
            "name": "employees",
            "raw_table_aliases": [
                "employees", "employee", "emp_master", "workers",
                "headcount", "personnel",
            ],
            "virtual_columns": [
                {"name": "employee_id", "aliases": ["employee_id", "emp_id",
                                                     "id", "worker_id"],
                 "type": "string"},
                {"name": "manager_id",  "aliases": ["manager_id", "mgr_id",
                                                     "reports_to", "supervisor_id"],
                 "type": "string"},
                {"name": "org_unit",    "aliases": ["department", "org_unit",
                                                     "team", "dept", "dept_code",
                                                     "department_code"],
                 "type": "string"},
                {"name": "title",       "aliases": ["title", "job_title",
                                                     "position"],
                 "type": "string"},
                {"name": "hire_date",   "aliases": ["hire_date", "start_date",
                                                     "joined_at", "date_hired"],
                 "type": "date"},
                {"name": "salary",      "aliases": ["salary", "annual_salary",
                                                     "base_salary", "comp"],
                 "type": "numeric"},
                {"name": "status",      "aliases": ["status", "employment_status",
                                                     "active_flag"],
                 "type": "string"},
            ],
            "relationships": [
                {"model": "org_units", "on": "org_unit = org_units.code",
                 "type": "many_to_one", "optional": True},
                {"model": "employees", "on": "manager_id = employees.employee_id",
                 "type": "many_to_one", "optional": True},
            ],
        },
        {
            "name": "hires",
            "raw_table_aliases": [
                "hires", "new_hires", "hiring_events", "onboarding",
            ],
            "virtual_columns": [
                {"name": "employee_id", "aliases": ["employee_id", "emp_id", "id"],
                 "type": "string"},
                {"name": "org_unit",    "aliases": ["department", "org_unit",
                                                     "dept", "dept_code"],
                 "type": "string"},
                {"name": "hire_date",   "aliases": ["hire_date", "start_date",
                                                     "joined_at"],
                 "type": "date"},
                {"name": "requisition_open_date", "aliases": ["req_open_date",
                                                               "requisition_date",
                                                               "posted_date"],
                 "type": "date"},
            ],
            "relationships": [
                {"model": "employees", "on": "employee_id = employees.employee_id",
                 "type": "many_to_one", "optional": True},
            ],
        },
        {
            "name": "terminations",
            "raw_table_aliases": [
                "terminations", "exits", "attrition_events", "departures",
                "offboarding",
            ],
            "virtual_columns": [
                {"name": "employee_id",      "aliases": ["employee_id", "emp_id",
                                                          "id"],
                 "type": "string"},
                {"name": "org_unit",         "aliases": ["department", "org_unit",
                                                          "dept", "dept_code"],
                 "type": "string"},
                {"name": "termination_date", "aliases": ["termination_date",
                                                          "exit_date", "end_date",
                                                          "last_day"],
                 "type": "date"},
                {"name": "reason",           "aliases": ["reason",
                                                          "termination_reason",
                                                          "exit_reason"],
                 "type": "string"},
                {"name": "is_voluntary",     "aliases": ["voluntary", "is_voluntary",
                                                          "voluntary_flag"],
                 "type": "boolean"},
            ],
            "relationships": [
                {"model": "employees", "on": "employee_id = employees.employee_id",
                 "type": "many_to_one", "optional": True},
            ],
        },
        {
            "name": "comp_history",
            "raw_table_aliases": [
                "comp_history", "compensation", "comp_changes", "salary_history",
                "pay_history",
            ],
            "virtual_columns": [
                {"name": "employee_id", "aliases": ["employee_id", "emp_id", "id"],
                 "type": "string"},
                {"name": "effective_date", "aliases": ["effective_date",
                                                        "change_date", "as_of_date"],
                 "type": "date"},
                {"name": "salary",      "aliases": ["salary", "annual_salary",
                                                     "base_salary", "new_salary"],
                 "type": "numeric"},
                {"name": "change_type", "aliases": ["change_type", "action_type",
                                                     "comp_change_type"],
                 "type": "string"},
            ],
            "relationships": [
                {"model": "employees", "on": "employee_id = employees.employee_id",
                 "type": "many_to_one", "optional": True},
            ],
        },
        {
            "name": "org_units",
            "raw_table_aliases": [
                "org_units", "departments", "dept_master", "org_master",
                "business_units",
            ],
            "virtual_columns": [
                {"name": "code",        "aliases": ["dept_code", "org_code",
                                                     "code", "department_code"],
                 "type": "string"},
                {"name": "name",        "aliases": ["dept_name", "org_name",
                                                     "name", "department_name"],
                 "type": "string"},
                {"name": "parent_code", "aliases": ["parent_code", "parent_org",
                                                     "reports_to_org"],
                 "type": "string"},
            ],
            "relationships": [],
        },
    ],
    "workflows": [
        {
            "name": "Headcount Summary",
            "description": "Active headcount per org unit",
            "action": "post_insight",
            "model": "employees",
            "sql": "SELECT org_unit, COUNT(DISTINCT employee_id) AS headcount "
                   "FROM employees "
                   "WHERE status IS NULL OR status NOT IN ('terminated', 'inactive') "
                   "GROUP BY org_unit "
                   "ORDER BY headcount DESC LIMIT 100",
        },
        {
            "name": "Attrition Analysis",
            "description": "Termination volume + voluntary share per org (last 12 months)",
            "action": "alert",
            "model": "terminations",
            "sql": "SELECT org_unit, COUNT(*) AS terms, "
                   "       SUM(CASE WHEN is_voluntary THEN 1 ELSE 0 END) AS voluntary, "
                   "       SUM(CASE WHEN NOT is_voluntary THEN 1 ELSE 0 END) AS involuntary "
                   "FROM terminations "
                   "WHERE termination_date >= CURRENT_DATE - INTERVAL '12 months' "
                   "GROUP BY org_unit "
                   "ORDER BY terms DESC LIMIT 50",
        },
        {
            "name": "Span of Control Audit",
            "description": "Managers w/ direct-report counts (flag <3 or >12)",
            "action": "post_insight",
            "model": "employees",
            "sql": "SELECT manager_id, COUNT(*) AS direct_reports, "
                   "       CASE WHEN COUNT(*) < 3 THEN 'too_narrow' "
                   "            WHEN COUNT(*) > 12 THEN 'too_wide' "
                   "            ELSE 'ok' END AS span_flag "
                   "FROM employees "
                   "WHERE manager_id IS NOT NULL "
                   "GROUP BY manager_id "
                   "ORDER BY direct_reports DESC LIMIT 100",
        },
        {
            "name": "Comp Equity Check",
            "description": "Avg salary per title to surface pay-band outliers",
            "action": "post_insight",
            "model": "employees",
            "sql": "SELECT title, COUNT(*) AS n, "
                   "       ROUND(AVG(salary)::numeric, 0) AS avg_salary, "
                   "       MIN(salary) AS min_salary, "
                   "       MAX(salary) AS max_salary "
                   "FROM employees "
                   "WHERE salary IS NOT NULL "
                   "GROUP BY title "
                   "HAVING COUNT(*) >= 3 "
                   "ORDER BY avg_salary DESC LIMIT 50",
        },
        {
            "name": "Time to Fill",
            "description": "Avg days from requisition open to hire date per org",
            "action": "post_insight",
            "model": "hires",
            "sql": "SELECT org_unit, COUNT(*) AS hires, "
                   "       ROUND(AVG(EXTRACT(epoch FROM hire_date - requisition_open_date) "
                   "                  / 86400)::numeric, 1) AS avg_days_to_fill "
                   "FROM hires "
                   "WHERE requisition_open_date IS NOT NULL "
                   "  AND hire_date IS NOT NULL "
                   "GROUP BY org_unit "
                   "ORDER BY avg_days_to_fill DESC LIMIT 50",
        },
        {
            "name": "New Hire Ramp",
            "description": "Hires by month with tenure-month average",
            "action": "post_insight",
            "model": "hires",
            "sql": "SELECT DATE_TRUNC('month', hire_date) AS hire_month, "
                   "       COUNT(*) AS hires, "
                   "       ROUND(AVG(EXTRACT(epoch FROM CURRENT_DATE - hire_date) "
                   "                  / (86400 * 30))::numeric, 1) AS avg_tenure_months "
                   "FROM hires "
                   "WHERE hire_date >= CURRENT_DATE - INTERVAL '24 months' "
                   "GROUP BY hire_month "
                   "ORDER BY hire_month DESC LIMIT 24",
        },
    ],
}
