FROM python:3.12-slim

# Build dependencies ahead of time. Execution uses --pull=never and --network=none.
RUN pip install --no-cache-dir \
    pandas==2.3.3 numpy==2.2.6 openpyxl==3.1.5 pyarrow==19.0.1 \
    matplotlib==3.10.8 scipy==1.15.3 polars==1.35.2 plotly==6.5.0
USER 65534:65534
WORKDIR /work
