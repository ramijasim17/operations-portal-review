#!/bin/bash
set -e

echo "Dropping old/broken tables for all six modules..."
sqlite3 db.sqlite3 << 'SQL'
DROP TABLE IF EXISTS job_manager_job;
DROP TABLE IF EXISTS job_manager_jobmanifest;
DROP TABLE IF EXISTS work_approval_filetype;
DROP TABLE IF EXISTS work_approval_approvalrecord;
DROP TABLE IF EXISTS work_approval_wafiletype;
DROP TABLE IF EXISTS work_approval_warecord;
DROP TABLE IF EXISTS sqb_sqbjob;
DROP TABLE IF EXISTS sqb_sqbdocument;
DROP TABLE IF EXISTS sqb_sqbtrackerentry;
DROP TABLE IF EXISTS personnel_personnel;
DROP TABLE IF EXISTS personnel_trainingcourse;
DROP TABLE IF EXISTS personnel_personnelcert;
DROP TABLE IF EXISTS maintenance_maintenancelevel;
DROP TABLE IF EXISTS maintenance_maintenancerecord;
DROP TABLE IF EXISTS daily_report_dailyreport;
DELETE FROM django_migrations WHERE app IN ('job_manager','maintenance','work_approval','sqb','personnel','daily_report');
SQL

echo "Removing stale migration files..."
rm -f job_manager/migrations/0*.py
rm -f maintenance/migrations/0*.py
rm -f work_approval/migrations/0*.py
rm -f sqb/migrations/0*.py
rm -f personnel/migrations/0*.py
rm -f daily_report/migrations/0*.py

echo "Regenerating migrations from the current models..."
python3 manage.py makemigrations job_manager maintenance work_approval sqb personnel daily_report

echo "Applying..."
python3 manage.py migrate

echo "Done. Verifying tables:"
sqlite3 db.sqlite3 ".tables" | tr ' ' '\n' | grep -E "job_manager|maintenance|work_approval|sqb|personnel|daily_report"
