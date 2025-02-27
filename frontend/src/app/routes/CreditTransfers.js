const API_BASE_PATH = '/credit-transfers'

const CREDIT_REQUESTS = {
  NEW: `${API_BASE_PATH}/new`,
  LIST: API_BASE_PATH,
  DETAILS: `${API_BASE_PATH}/:id`,
  EDIT: `${API_BASE_PATH}/:id/edit`
}

export default CREDIT_REQUESTS
