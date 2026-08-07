-- Disposable local-development data and credentials only.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portfolio_reader') THEN
        CREATE ROLE portfolio_reader LOGIN PASSWORD 'portfolio_reader';
    END IF;
END
$$;
ALTER ROLE portfolio_reader WITH LOGIN PASSWORD 'portfolio_reader';
ALTER ROLE portfolio_reader SET default_transaction_read_only = on;
REVOKE ALL ON DATABASE portfolio FROM portfolio_reader;
GRANT CONNECT ON DATABASE portfolio TO portfolio_reader;
REVOKE ALL ON SCHEMA public FROM portfolio_reader;
GRANT USAGE ON SCHEMA public TO portfolio_reader;

CREATE TABLE IF NOT EXISTS region_operations (month date NOT NULL, region text NOT NULL, revenue numeric NOT NULL, logistics_cost numeric NOT NULL, profit numeric NOT NULL, manager_email text NOT NULL);
TRUNCATE region_operations;
INSERT INTO region_operations VALUES
('2025-01-01','East',1000,220,180,'east-manager@example.test'),('2025-02-01','East',1010,225,185,'east-manager@example.test'),('2025-03-01','East',995,222,176,'east-manager@example.test'),('2025-04-01','East',1005,228,179,'east-manager@example.test'),('2025-05-01','East',1015,230,181,'east-manager@example.test'),('2025-06-01','East',1008,232,178,'east-manager@example.test'),('2025-07-01','East',1002,235,174,'east-manager@example.test'),('2025-08-01','East',1007,238,170,'east-manager@example.test'),('2025-09-01','East',1000,240,165,'east-manager@example.test'),('2025-10-01','East',1004,290,120,'east-manager@example.test'),('2025-11-01','East',998,315,95,'east-manager@example.test'),('2025-12-01','East',1003,340,72,'east-manager@example.test'),
('2025-01-01','West',980,210,170,'west-manager@example.test'),('2025-02-01','West',985,212,171,'west-manager@example.test'),('2025-03-01','West',990,214,173,'west-manager@example.test'),('2025-04-01','West',995,215,175,'west-manager@example.test'),('2025-05-01','West',1000,216,177,'west-manager@example.test'),('2025-06-01','West',1005,218,178,'west-manager@example.test'),('2025-07-01','West',1010,220,180,'west-manager@example.test'),('2025-08-01','West',1015,221,181,'west-manager@example.test'),('2025-09-01','West',1020,222,183,'west-manager@example.test'),('2025-10-01','West',1025,224,185,'west-manager@example.test'),('2025-11-01','West',1030,225,186,'west-manager@example.test'),('2025-12-01','West',1035,226,188,'west-manager@example.test');
REVOKE ALL ON region_operations FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM portfolio_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO portfolio_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE portfolio IN SCHEMA public GRANT SELECT ON TABLES TO portfolio_reader;
