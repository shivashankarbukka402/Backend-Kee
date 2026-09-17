# ---------------------------------------------------------------------------
# Custom Frappe/ERPNext image with the keemeds_commerce app installed.
#
# Base image already contains: Python, Node, bench CLI, frappe, erpnext.
# We just add our custom app on top and build its assets.
# ---------------------------------------------------------------------------
FROM frappe/erpnext:v15

USER frappe
WORKDIR /home/frappe/frappe-bench

# Copy the keemeds_commerce app source into the bench's apps folder.
# (Build context should be the root of the Enterprise-Healthcare-Commerce-Platform-B repo)
COPY --chown=frappe:frappe . apps/keemeds_commerce

# Register the app with the bench and install its Python dependencies
RUN echo "keemeds_commerce" >> sites/apps.txt \
    && pip install --no-cache-dir -e apps/keemeds_commerce

# Build front-end assets (JS/CSS) for the app, if any
RUN bench build --app keemeds_commerce || true

EXPOSE 8000

# Default command; overridden per-service in docker-compose.yml
CMD ["bench", "start"]