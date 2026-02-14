FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy project metadata and install dependencies
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# Expose API port
EXPOSE 8000

# Run the API server
CMD ["python", "src/main.py"]
