// Add a new component example for listing reconciliations

### 4. Reconciliation List Component

```typescript
// src/components/stockReconciliation/ReconciliationList.tsx

import React, { useEffect, useState } from 'react';
import { useStockReconciliation } from '../../hooks/useStockReconciliation';
import { WorkflowStatusBadge } from './WorkflowStatusBadge';

interface ReconciliationListProps {
  filters?: {
    workflow_status?: string;
    warehouse?: string;
    company?: string;
    from_date?: string;
    to_date?: string;
  };
  onSelect?: (reconciliationName: string) => void;
}

export const ReconciliationList: React.FC<ReconciliationListProps> = ({
  filters,
  onSelect,
}) => {
  const { listReconciliations, reconciliationsList, listMeta, loading, error } = useStockReconciliation();
  const [currentPage, setCurrentPage] = useState(0);
  const [pageSize] = useState(20);

  useEffect(() => {
    listReconciliations({
      ...filters,
      limit: pageSize,
      offset: currentPage * pageSize,
    });
  }, [filters, currentPage, pageSize, listReconciliations]);

  const handlePageChange = (newPage: number) => {
    setCurrentPage(newPage);
  };

  const getDocStatusLabel = (docstatus: number) => {
    switch (docstatus) {
      case 0:
        return 'Draft';
      case 1:
        return 'Submitted';
      case 2:
        return 'Cancelled';
      default:
        return 'Unknown';
    }
  };

  if (loading && reconciliationsList.length === 0) {
    return <div>Loading reconciliations...</div>;
  }

  if (error) {
    return <div style={{ color: 'red' }}>Error: {error}</div>;
  }

  return (
    <div className="reconciliation-list">
      <h2>Stock Reconciliations</h2>

      {listMeta && (
        <div className="list-meta">
          <p>Total: {listMeta.total_count} | Showing: {reconciliationsList.length}</p>
        </div>
      )}

      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th>Name</th>
            <th>Warehouse</th>
            <th>Posting Date</th>
            <th>Status</th>
            <th>Workflow Status</th>
            <th>Items</th>
            <th>Owner</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {reconciliationsList.map((reconciliation) => (
            <tr key={reconciliation.name}>
              <td>{reconciliation.name}</td>
              <td>{reconciliation.warehouse}</td>
              <td>{reconciliation.posting_date}</td>
              <td>{getDocStatusLabel(reconciliation.docstatus)}</td>
              <td>
                <WorkflowStatusBadge status={reconciliation.workflow_status} />
              </td>
              <td>{reconciliation.items_count}</td>
              <td>{reconciliation.owner}</td>
              <td>
                {onSelect && (
                  <button onClick={() => onSelect(reconciliation.name)}>
                    View
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {listMeta && listMeta.total_count > pageSize && (
        <div className="pagination">
          <button
            onClick={() => handlePageChange(currentPage - 1)}
            disabled={currentPage === 0}
          >
            Previous
          </button>
          <span>
            Page {currentPage + 1} of {Math.ceil(listMeta.total_count / pageSize)}
          </span>
          <button
            onClick={() => handlePageChange(currentPage + 1)}
            disabled={!listMeta.has_more}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
};
```
