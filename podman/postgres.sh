# Start PostgreSQL 16 from official RHEL10 image on podman.
#
# connect with CLI:
#
#   export PGPASSWORD='admin'
#   psql -d aichat -U admin -h localhost -p 5432
#

podman run -d --name ai-chat-postgres \
  -e POSTGRESQL_USER=admin \
  -e POSTGRESQL_PASSWORD=admin \
  -e POSTGRESQL_DATABASE=aichat \
  -p 5432:5432 \
  registry.redhat.io/rhel10/postgresql-16:latest

