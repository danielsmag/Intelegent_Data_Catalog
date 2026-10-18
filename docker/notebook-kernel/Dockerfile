FROM apache/spark:4.1.3-scala2.13-java17-python3-ubuntu

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYSPARK_PYTHON=/opt/venv/bin/python \
    PYSPARK_DRIVER_PYTHON=/opt/venv/bin/python \
    JUPYTER_PATH=/opt/kernel-share/share/jupyter \
    HOME=/home/notebook

RUN pip install --no-cache-dir \
    "jupyter_kernel_gateway>=3.0,<4" \
    ipykernel \
    "trino>=0.340" \
    pandas \
    pyarrow \
    matplotlib \
    && python - <<'PY'
import urllib.request
from pathlib import Path

jars = Path("/opt/spark/jars")
urls = [
    "https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.1_2.13/1.11.0/iceberg-spark-runtime-4.1_2.13-1.11.0.jar",
    "https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-aws-bundle/1.11.0/iceberg-aws-bundle-1.11.0.jar",
    "https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.7/postgresql-42.7.7.jar",
]
for url in urls:
    name = url.rsplit("/", 1)[-1]
    urllib.request.urlretrieve(url, jars / name)
PY

COPY docker/notebook-kernel/kernel/ /opt/kernel/
COPY docker/spark/spark-catalog-conf.sh /opt/spark/spark-catalog-conf.sh

RUN python -m ipykernel install --prefix /opt/kernel-share --name pyspark --display-name PySpark \
    && cp /opt/kernel/kernel.json /opt/kernel-share/share/jupyter/kernels/pyspark/kernel.json \
    && useradd --create-home --uid 1850 notebook \
    && chown -R notebook:notebook /opt/kernel /opt/kernel-share /home/notebook \
    && chmod 755 /opt/kernel/entrypoint.sh /opt/spark/spark-catalog-conf.sh

USER notebook
EXPOSE 8888
ENTRYPOINT ["/bin/bash", "/opt/kernel/entrypoint.sh"]
