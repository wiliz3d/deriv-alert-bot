# Use Python 3.13
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Copy dependencies file first
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your bot code
COPY . .

# Run your bot
CMD ["python", "deriv_alert_bot.py"]