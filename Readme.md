# Dhatu Drishti

Dhatu Drishti is a Flask-based mining operations dashboard for cataloging machinery, tracking equipment status, and generating AI-assisted operational risk reports. The project combines a public equipment directory, an admin panel, and an AI analysis workflow that reads uploaded CSV data and produces structured reports for mine operations.

## Overview

This application is designed for mining and industrial asset monitoring use cases. It gives teams a way to:

- browse and filter equipment across mines and machine types
- view detailed machine records and operational summaries
- manage machine data through an admin dashboard
- upload operational CSV files for AI-based analysis
- generate risk insights, recommendations, and exportable reports
- review weather and operational context for mine sites

## Key Features

- Public machinery directory with search, filters, and paging
- Detailed machinery pages with equipment metadata
- Admin login and protected dashboard access
- Machine CRUD operations in the admin interface
- AI analysis workflow using Gemini-powered report generation
- CSV upload processing with validation and summarization
- PDF and DOCX report export capability
- Weather and geodata lookup support for mine context
- SQLite-ready setup with PostgreSQL support via environment configuration

## Tech Stack

- Python 3.11+
- Flask
- Flask-SQLAlchemy
- SQLAlchemy
- Pandas and NumPy
- Requests
- ReportLab and python-docx for report downloads
- Python-dotenv for environment loading
- Jinja2 templates and static assets for the frontend

## Project Structure

```text
.
├── admin.py
├── ai_analysis/
│   ├── __init__.py
│   ├── config.py
│   ├── data_loader.py
│   ├── gemini_client.py
│   ├── geodata.py
│   ├── prompts.py
│   ├── registry.py
│   ├── report_docs.py
│   ├── risk.py
│   ├── routes.py
│   └── weather.py
├── extensions.py
├── instance/
├── main.py
├── models.py
├── requirements-ai.txt
├── requirements.txt
├── security.py
├── seed_data.py
├── static/
├── templates/
├── uploads/
├── .env.example (if present in your environment)
├── .gitignore
├── .gitattributes
└── README.md
```

## Prerequisites

- Python 3.11 or newer
- pip
- A Gemini API key for AI report generation

## Local Setup

1. Clone the project and move into the project folder.
2. Create a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Set environment variables. Create a `.env` file in the project root with values like:

```env
SECRET_KEY=your-secret-key
DATABASE_URL=sqlite:///instance/app.db
GEMINI_API_KEY=your-gemini-api-key
ADMIN_EMAIL=admin@dhatudrishti.local
ADMIN_NAME=Administrator
ADMIN_PASSWORD=ChangeMe#Admin2026
```

> If `DATABASE_URL` is omitted, the app defaults to a SQLite database in the `instance` folder.

## Run the Application

Start the Flask app:

```bash
python main.py
```

The app listens on:

- http://localhost:5001

## Default Admin Access

On first run, the project creates an admin user if none exists. The default values are:

- Email: admin@dhatudrishti.local
- Name: Administrator
- Password: ChangeMe#Admin2026

It is strongly recommended that you change this password immediately in a real environment.

## AI Analysis Workflow

The AI analysis module allows an operator to:

- upload a CSV file containing mine or equipment data
- let the application summarize and validate the dataset
- send the structured content to the Gemini API for risk analysis
- review generated insights, recommendations, and forecast direction
- export the final report as PDF or DOCX

The Gemini API key must be configured through the environment or `.env` file for this feature to work.

## Production Notes

- Use a strong `SECRET_KEY` in production.
- Prefer a managed PostgreSQL database for production workloads.
- Keep the Gemini API key in a secure secret store or environment manager.
- Review and rotate admin credentials before exposing the app externally.

## License

This project is intended for internal operational use and is not published as a formal public package. Please confirm the license with the project owner before distributing or commercializing the code.

## Common Commands

```bash
# Activate venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run app
python main.py

# Optional: install AI-only extras if you are managing dependencies separately
pip install -r requirements-ai.txt
```

## Troubleshooting

- If the app cannot find the database, confirm `DATABASE_URL` and the `instance` folder permissions.
- If the AI analysis fails, verify that `GEMINI_API_KEY` is set correctly.
- If report exports fail, ensure `python-docx` and `reportlab` are installed.

---

Built for mining operations visibility, equipment monitoring, and AI-backed operational decision support.
