import axios from 'axios'
import { useAuthStore } from '../stores/auth'

const http = axios.create({
  baseURL: '',
  timeout: 30000
})

http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const message = error.response?.data?.detail || error.response?.data?.msg || error.message
    return Promise.reject(new Error(message))
  }
)

export default http
