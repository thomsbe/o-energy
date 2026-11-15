"""GraphQL documents used against the Octopus Energy Germany (Kraken) API.

The schema was verified live against https://api.oeg-kraken.energy/v1/graphql/,
so field names are taken from the real introspection result.
"""

from __future__ import annotations

OBTAIN_TOKEN = """
mutation ObtainToken($input: ObtainJSONWebTokenInput!) {
  obtainKrakenToken(input: $input) {
    token
    refreshToken
    refreshExpiresIn
  }
}
"""

OBTAIN_LONG_LIVED_REFRESH_TOKEN = """
mutation ObtainLongLivedRefreshToken($input: ObtainLongLivedRefreshTokenInput!) {
  obtainLongLivedRefreshToken(input: $input) {
    refreshToken
    refreshExpiresIn
  }
}
"""

VIEWER_ACCOUNTS = """
query ViewerAccounts {
  viewer {
    id
    email
    fullName
    accounts {
      ... on AccountType {
        number
        brand
        status
        balance
        overdueBalance
        billingName
        billingEmail
        marketSupplyAgreements(first: 50) {
          edges {
            node {
              id
              isActive
              agreedFrom
              agreedTo
              supplyPoint {
                id
                externalIdentifier
                marketName
              }
              product {
                code
                displayName
                fullName
              }
            }
          }
        }
      }
    }
  }
}
"""

AGREEMENT_DETAILS = """
query AgreementDetails($id: ID!) {
  agreement(id: $id) {
    id
    isActive
    validFrom
    validTo
    netMonthlyStandingCharge
    standingChargeGrossRateInformation {
      date
      vatRate
      grossRate
      rateValidToDate
    }
    unitRateInformation {
      ... on SimpleProductUnitRateInformation {
        netUnitRateCentsPerKwh
        latestGrossUnitRateCentsPerKwh
      }
      ... on TimeOfUseProductUnitRateInformation {
        rates {
          timeslotName
          netUnitRateCentsPerKwh
          latestGrossUnitRateCentsPerKwh
          timeslotActivationRules {
            activeFromTime
            activeToTime
          }
        }
      }
    }
    unitRateForecast {
      validFrom
      validTo
      unitRateInformation {
        ... on SimpleProductUnitRateInformation {
          netUnitRateCentsPerKwh
          latestGrossUnitRateCentsPerKwh
        }
        ... on TimeOfUseProductUnitRateInformation {
          rates {
            timeslotName
            netUnitRateCentsPerKwh
            latestGrossUnitRateCentsPerKwh
            timeslotActivationRules {
              activeFromTime
              activeToTime
            }
          }
        }
      }
    }
    product {
      code
      displayName
      isTimeOfUse
    }
  }
}
"""

AGREEMENT_RATES = """
query AgreementRates($agreementId: ID!, $activeAt: DateTime, $first: Int, $after: String) {
  agreementRates(agreementId: $agreementId, activeAt: $activeAt, first: $first, after: $after) {
    totalCount
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      cursor
      node {
        name
        category
        pricePerUnit
        unit
        currency
        validityPeriod {
          start
          end
        }
      }
    }
  }
}
"""

EPEX_DAY_AHEAD = """
query EpexDayAhead($periodStart: DateTime!, $periodEnd: DateTime!, $first: Int, $after: String) {
  epexDayAheadPrices(periodStart: $periodStart, periodEnd: $periodEnd, first: $first, after: $after) {
    totalCount
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      cursor
      node {
        periodStart
        periodEnd
        value
        unit
      }
    }
  }
}
"""

ACCOUNT_PROPERTIES = """
query AccountProperties($accountNumber: String!) {
  account(accountNumber: $accountNumber) {
    number
    properties {
      id
      label
      address
      electricityMalos {
        id
        maloNumber
        supplyPointId
        meters {
          id
          number
          meterType
          meloNumber
          hasSmartMeterGateway
          shouldReceiveSmartMeterData
        }
      }
    }
  }
}
"""

PROPERTY_MEASUREMENTS = """
query PropertyMeasurements(
  $propertyId: ID!
  $startAt: DateTime!
  $endAt: DateTime!
  $timezone: String!
  $first: Int
  $after: String
) {
  property(id: $propertyId) {
    id
    measurements(
      startAt: $startAt
      endAt: $endAt
      timezone: $timezone
      utilityFilters: [{ electricityFilters: { readingFrequencyType: RAW_INTERVAL } }]
      first: $first
      after: $after
    ) {
      totalCount
      pageInfo {
        hasNextPage
        endCursor
      }
      edges {
        cursor
        node {
          ... on IntervalMeasurementType {
            source
            value
            unit
            readAt
            startAt
            endAt
          }
        }
      }
    }
  }
}
"""

ELECTRICITY_METERS = """
query ElectricityMeters($accountNumber: String!, $meloNumber: String!) {
  electricityMeters(accountNumber: $accountNumber, meloNumber: $meloNumber, first: 50) {
    edges {
      node {
        id
        number
        meloNumber
        hasSmartMeterGateway
        conversionFactor
        activeFrom
        activeTo
      }
    }
  }
}
"""

ELECTRICITY_METER_READINGS = """
query ElectricityMeterReadings(
  $accountNumber: String!
  $meterId: ID!
  $readFrom: DateTime
  $readTo: DateTime
  $first: Int
  $after: String
) {
  electricityMeterReadings(
    accountNumber: $accountNumber
    meterId: $meterId
    readFrom: $readFrom
    readTo: $readTo
    first: $first
    after: $after
  ) {
    totalCount
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      cursor
      node {
        value
        readAt
        registerObisCode
        typeOfRead
        origin
        status
        meterId
        registerType
      }
    }
  }
}
"""

SUPPLY_POINT_READINGS = """
query SupplyPointReadings(
  $externalIdentifier: String!
  $marketName: String!
  $startAt: DateTime!
  $endAt: DateTime!
  $readingType: ReadingTypes!
  $timeGranularity: TimeGranularities!
  $timezone: String!
) {
  supplyPoint(externalIdentifier: $externalIdentifier, marketName: $marketName) {
    id
    externalIdentifier
    readings(
      startAt: $startAt
      endAt: $endAt
      readingType: $readingType
      timeGranularity: $timeGranularity
      timezone: $timezone
    ) {
      importReadings(first: 500) {
        totalCount
        pageInfo {
          hasNextPage
          endCursor
        }
        edges {
          cursor
          node {
            value
            units
            intervalStart
            intervalEnd
          }
        }
      }
    }
  }
}
"""

ACCOUNT_BILLS = """
query AccountBills($accountNumber: String!, $first: Int) {
  account(accountNumber: $accountNumber) {
    number
    balance
    overdueBalance
    bills(first: $first) {
      totalCount
      edges {
        node {
          ... on StatementType {
            id
            billType
            fromDate
            toDate
            issuedDate
            status
            isFinal
            totalCharges {
              netTotal
              taxTotal
              grossTotal
            }
            totalCredits {
              netTotal
              taxTotal
              grossTotal
            }
          }
          ... on InvoiceType {
            id
            billType
            fromDate
            toDate
            issuedDate
            identifier
            isAnnulled
            grossAmount
            totalCharges {
              netTotal
              taxTotal
              grossTotal
            }
          }
        }
      }
    }
  }
}
"""

ACCOUNT_TRANSACTIONS = """
query AccountTransactions($accountNumber: String!, $fromDate: Date, $toDate: Date, $first: Int) {
  account(accountNumber: $accountNumber) {
    number
    transactions(fromDate: $fromDate, toDate: $toDate, first: $first) {
      edges {
        node {
          ... on Charge {
            id
            postedDate
            createdAt
            title
            reasonCode
            amounts {
              net
              tax
              gross
            }
          }
          ... on Payment {
            id
            postedDate
            createdAt
            title
            reasonCode
            amounts {
              net
              tax
              gross
            }
          }
          ... on Credit {
            id
            postedDate
            createdAt
            title
            reasonCode
            amounts {
              net
              tax
              gross
            }
          }
        }
      }
    }
  }
}
"""
