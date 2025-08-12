# === STAGE 1: Build the dependencies ===
# Use a specific version for reproducibility
FROM python:3.11-slim as builder

# Set the working directory
WORKDIR /app

# Copy the requirements file
COPY requirements.txt .

# Install dependencies into a dedicated "packages" directory
# Using --target is key for making them portable to the next stage
RUN pip install --no-cache-dir --target=/app/packages -r requirements.txt


# === STAGE 2: Build the final, lightweight image ===
# Use the same slim base image for the final product
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy the installed packages from the builder stage
COPY --from=builder /app/packages /usr/local/lib/python3.11/site-packages

# Copy the agent script itself
COPY agent.py .

# Add the packages path to the python path so the interpreter can find them
ENV PYTHONPATH=/usr/local/lib/python3.11/site-packages

# Command to run when the container starts.
# -u flag ensures that logs are sent straight to stdout without buffering,
# which is essential for container logging systems like Kubernetes.
CMD [ "python", "-u", "agent.py" ]