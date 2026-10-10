FROM apache/airflow:3.3.2-python3.12

COPY requirements.txt /requirements.txt
USER airflow
RUN pip install --no-cache-dir -r /requirements.txt
