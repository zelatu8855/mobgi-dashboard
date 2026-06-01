import http from './http'

export const materialsReportApi = (params) => {
  const { start_date, end_date, sort_field, sort_direction, page, page_size } = params
  return http.get('/api/report/materials', {
    params: { start_date, end_date, sort_field, sort_direction, page, page_size }
  })
}
