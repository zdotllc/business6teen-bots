FROM python:3.11-slim

WORKDIR /app

# Copy your code and data
COPY . .
COPY .agent-company-ai/ /app/.agent-company-ai/

# Install dependencies
RUN pip install -e .

# Expose the dashboard port
EXPOSE 8420

# Start the dashboard
CMD ["agent-company-ai", "dashboard", "--host", "0.0.0.0", "--port", "8420"]