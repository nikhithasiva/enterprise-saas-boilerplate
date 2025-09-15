-- Create test database
CREATE DATABASE saas_test_db;

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE saas_db TO saas_user;
GRANT ALL PRIVILEGES ON DATABASE saas_test_db TO saas_user;

-- Enable UUID extension for both databases
\c saas_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

\c saas_test_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";