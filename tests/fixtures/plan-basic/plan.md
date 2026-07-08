# Plan — order cancellation (fixture)

```task
id: db-orders-migration
agent: specialist-database
footprint:
  - db/migrations/
  - db/schema.prisma
produces: [migration]
consumes: []
```

```task
id: backend-orders-api
agent: specialist-backend
footprint:
  - api/orders.ts
produces: [cancel-api]
consumes: [migration]
```

```task
id: frontend-cancel-button
agent: specialist-frontend
footprint:
  - web/components/CancelButton.tsx
produces: []
consumes: [cancel-api]
```
